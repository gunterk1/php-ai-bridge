# Running this on Kubernetes, and the probe that kills a healthy service

The rest of this repository argues that the AI capability never lived in the app
layer. This directory takes the same system and asks a different question: what
happens when you deploy it the way everyone deploys things?

The answer, measured rather than asserted: **Kubernetes restarted the AI service
three times in three minutes, and nothing was broken.**

---

## The finding

Same images, same load, same stand-in model server with the same fixed latency.
The only difference is the liveness and readiness configuration.

|                                   | `naive` | `honest` |
|-----------------------------------|--------:|---------:|
| pod restarts during the run       | **3**   | **0**    |
| grounded answers served           | 219     | **320**  |
| answers served from an empty index| **60**  | 0        |
| re-ingests the load driver needed | 3       | 0        |
| failed requests                   | 100     | 59       |
| median latency                    | 55.8 s  | **25.3 s** |
| max latency                       | 72.8 s  | 50.1 s   |

The kubelet's own words from the `naive` run:

> `Container ai-service failed liveness probe, will be restarted`

Raw output: [`experiment/result-naive.json`](experiment/result-naive.json) and
[`experiment/result-honest.json`](experiment/result-honest.json).

## Why a working service looks dead

FastAPI runs endpoints declared `def` — as opposed to `async def` — in a bounded
threadpool. anyio sizes it at 40 by default. In this service `/query`, `/ingest`
**and `/health`** are all synchronous, so they draw from the same 40 slots.

Measured directly, before any of this reached a cluster:

| concurrent `/query` | worst `/health` response |
|--------------------:|-------------------------:|
| 10                  | 5.3 ms                   |
| **39**              | **5.5 ms**               |
| **45**              | **16,072 ms**            |
| 60                  | 16,170 ms                |

The cliff sits exactly at the size of the threadpool. Below it `/health` is
instant; above it, the probe waits for a work thread like any other request —
which means it waits for a completion. A liveness probe with the usual
`timeoutSeconds: 1` sees sixteen seconds of silence and reports the container
dead.

**A liveness probe that queues behind the work it observes is not a health
check. It is a queue-depth measurement with a kill switch attached.**

Note what the table does *not* show: a gradual degradation. The median stays
around 4 ms even at 60 concurrent. Failures are intermittent, they look like
flapping, and nobody suspects the threadpool.

## The fix, and the fix that looks right but is not

The reflex is to raise `timeoutSeconds` until the probe stops failing. That
works, in the sense that a blindfold stops you seeing the wall. It makes the
probe unable to detect anything at all, because the one thing it now tolerates
is unbounded blocking.

The actual fix is that the probe must not share a resource with the work it is
watching:

- **`/alive`** is declared `async`, so Starlette runs it on the event loop
  instead of handing it to the threadpool. It cannot queue behind inference.
  This is liveness: is the process still running.
- **`/ready`** asks whether the model backend is reachable and whether a work
  thread is free. This is readiness: can this pod serve a request *right now*.
  Failing it is cheap — the endpoint leaves the Service, in-flight work
  finishes, and it comes back. Failing liveness is not: the pod is killed.

`/health` stays, unchanged, for `docker-compose` and the three app surfaces. It
is documented for what it is: a synchronous endpoint that answers `ok`
unconditionally. It never checked anything.

**What the honest profile does not fix:** 59 requests still failed. Readiness
gating a *single* replica converts saturation into unavailability — the pod
correctly removes itself from the Service, and with one replica there is nothing
left to serve. Load shedding needs somewhere for the load to go. That is a
replica-count decision, not a probe decision, and it is left visible here rather
than tuned away.

## The second finding: the restart empties the index, quietly

`RagEngine` keeps its vector store in memory. When the liveness probe killed the
pod, the ingested documents went with it. The service came back, reported
healthy, and answered every subsequent question with:

> `{"answer": "No documents have been ingested yet.", "sources": []}`

HTTP 200. No error, no log line, no metric. The pod is green in every dashboard
and the product is returning nothing.

This is also how the experiment first went wrong. An early run reported 21,693
requests at a 0.26 s median against a backend with a fixed 20 s latency — because
after the first restart there was nothing to retrieve, `query()` took its
empty-store fast path, and the load generator was measuring a code path that
never calls the model. The run now counts those answers as their own outcome
(`answers_from_empty_store`) and re-ingests so the load continues. **60 of them
in the `naive` run. Zero in the `honest` one** — not because the failure was
fixed, but because the restart that causes it no longer happens.

## The third: the one service with state cannot roll

`symfony-app` keeps its audit trail in SQLite on a volume. Two defaults are
wrong for it, and neither fails loudly:

- **`replicas: 2`** — two processes writing one SQLite file is not a scaling
  strategy.
- **`RollingUpdate`** — the default starts the new pod before stopping the old
  one. With a ReadWriteOnce volume the new pod cannot mount what the old one
  still holds, so the rollout does not error; it sits in `ContainerCreating`
  until somebody looks.

It is deployed `Recreate`, single replica. That accepts a gap in availability
instead of pretending the service scales horizontally. The gap is what the audit
trail costs, and it belongs in the open — see the comment in
[`terraform/surfaces.tf`](terraform/surfaces.tf).

## What was measured wrong on the way here

Kept because the corrections are the part worth reading.

1. **Load driven through `kubectl port-forward`.** The forward dies with the pod
   it points at. Sixty threads then hammered a dead socket at ~4,200 requests a
   second, and the run reported 633,000 requests. The load generator moved into
   the cluster as a Job, talking to the Service.
2. **Counting `failed liveness probe` event lines.** Kubernetes aggregates
   repeated identical events into one record with a `count` field, so three
   kills appear as one line. The restart delta is the measurement; the event
   message is kept as evidence of the cause, not as a counter.
3. **Reading the event count without a baseline.** Events live about an hour, so
   the second profile inherited the first profile's kills and a clean run
   reported three. Read before and after, or not at all.

## What this is not

The cluster is minikube on a laptop. This demonstrates the behaviour, the
manifests and the reasoning; it is not a claim to have operated a fleet. The
Terraform here manages the workloads, not the cluster — the same resources move
to a managed cluster by changing the context, not the code.

The model server is a deterministic stand-in with a fixed latency, because the
experiment is about Kubernetes' response to slow inference and a real provider's
jitter would only add noise. Its source is in [`slow-provider/`](slow-provider/)
and it is 80 lines.

## Running it

```bash
minikube start --cpus=4 --memory=6g --addons=metrics-server
eval $(minikube docker-env)

for svc in ai-service php-app node-app symfony-app; do
  docker build -t "${svc}:k8s" "./${svc}"
done
docker build -t slow-provider:k8s ./deploy/slow-provider
docker build -t loadgen:k8s ./deploy/experiment/loadgen

terraform -chdir=deploy/terraform init
./deploy/experiment/run.sh naive
./deploy/experiment/run.sh honest
```

Each run takes about three minutes and rewrites its own `result-*.json`.

To reach the three product surfaces:

```bash
kubectl -n php-ai-bridge port-forward svc/php-app 8080:8080
kubectl -n php-ai-bridge port-forward svc/node-app 8081:8081
kubectl -n php-ai-bridge port-forward svc/symfony-app 8082:8082
```

Tear down with `terraform -chdir=deploy/terraform destroy`.
