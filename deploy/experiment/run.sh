#!/usr/bin/env bash
# Run the identical load against one probe profile and record what the cluster did.
#
#   ./deploy/experiment/run.sh naive
#   ./deploy/experiment/run.sh honest
#
# Everything except the probe profile is held constant: same images, same
# concurrency, same stand-in model server with the same fixed latency.
set -euo pipefail

PROFILE="${1:?usage: run.sh <naive|honest>}"
NS=php-ai-bridge
CONCURRENCY="${CONCURRENCY:-60}"
DURATION="${DURATION:-150}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> applying probe profile: ${PROFILE}"
terraform -chdir="${HERE}/../terraform" apply -auto-approve -no-color \
  -var "probe_profile=${PROFILE}" >/dev/null
kubectl -n "$NS" rollout status deploy/ai-service --timeout=180s

# A restart count only means something relative to where it started.
BEFORE=$(kubectl -n "$NS" get pods -l app=ai-service \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' \
  | paste -sd+ | bc)
echo "==> restarts before: ${BEFORE}"

kubectl -n "$NS" delete job loadgen --ignore-not-found >/dev/null
kubectl -n "$NS" create job loadgen --image=loadgen:k8s -- python -u gen.py >/dev/null
kubectl -n "$NS" patch job loadgen --type=strategic -p "$(cat <<JSON
{"spec":{"template":{"spec":{"containers":[{"name":"loadgen","imagePullPolicy":"IfNotPresent","env":[
  {"name":"CONCURRENCY","value":"${CONCURRENCY}"},
  {"name":"DURATION_S","value":"${DURATION}"}]}]}}}}
JSON
)" >/dev/null 2>&1 || true

echo "==> driving ${CONCURRENCY} concurrent /query for ${DURATION}s"
kubectl -n "$NS" wait --for=condition=complete job/loadgen --timeout=$((DURATION + 300))s

LOAD=$(kubectl -n "$NS" logs job/loadgen | tail -1)
AFTER=$(kubectl -n "$NS" get pods -l app=ai-service \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' \
  | paste -sd+ | bc)
# Do not count event lines. Kubernetes aggregates repeated identical events into
# one record with a count field, so three kills appear as a single line -- a line
# count reads that as one. The restart delta above is the measurement; the event
# is kept as evidence of the cause, in the kubelet's own words.
KILLED=$(kubectl -n "$NS" get events --field-selector involvedObject.kind=Pod \
  -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' \
  | grep 'failed liveness probe' | tail -1 || true)

echo "==> restarts after: ${AFTER}"

python3 - "$PROFILE" "$BEFORE" "$AFTER" "$KILLED" "$LOAD" <<'PY' | tee "${HERE}/result-${PROFILE}.json"
import json, sys
profile, before, after, killed, load = sys.argv[1:6]
out = {"probe_profile": profile,
       "pod_restarts_during_run": int(after) - int(before),
       "kubelet_said": killed or None,
       "load": json.loads(load)}
# A restart is only half the damage. The other half is that the service came
# back reporting healthy while answering from an empty index.
out["silent_index_loss"] = out["load"].get("answers_from_empty_store", 0) > 0
print(json.dumps(out, indent=2))
PY
