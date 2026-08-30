# php-ai-bridge

A small, honest reference implementation of **AI-service integration from PHP**:
a PHP application that adds semantic search and retrieval-augmented answering to
any set of documents by talking to a Python AI service **over a REST boundary** —
never to a model directly.

The point is the **integration pattern**, not the model. The same code runs
against an external provider (OpenAI) or a **self-hosted, OpenAI-compatible model**
(LocalAI, Ollama, vLLM). Choosing where the model runs is a configuration change,
so sensitive documents can stay on your own infrastructure.

## Architecture

```
  Browser
     │  (HTML + fetch)
     ├──────────────────────────┐
     ▼                          ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐   REST / JSON    ┌────────────────────────────┐
│ php-app (PHP)    │ │ node-app (TS)    │ │ symfony-app      │ ───────────────▶ │  ai-service (FastAPI)      │
│ • front control. │ │ • front control. │ │ • Symfony 8      │  POST /ingest    │  • LangChain                │
│ • AiClient(curl) │ │ • AiClient(fetch)│ │ • Doctrine audit │  POST /query     │  • RecursiveCharacterSplit  │
│ • minimal UI     │ │ • contract guards│ │ • API-key guard  │  GET  /health    │  • embeddings + vector DB   │
│                  │ │                  │ │ • /api/audit     │ ◀─────────────── │  • RAG answer + sources     │
└──────────────────┘ └──────────────────┘ └──────────────────┘                  └────────────────────────────┘
        same retry policy, same three endpoints, same provider switch
                                                      │ OpenAI-compatible API
                                          ┌───────────┴────────────┐
                                          ▼                        ▼
                                    OpenAI (external)      LocalAI / Ollama (local)
```

The app owns the product surface; the AI capability lives behind HTTP. The two
sides scale, deploy and fail independently.

**Three product surfaces, one boundary.** `php-app`, `node-app` and `symfony-app`
are behaviour-identical clients of the same service: same three endpoints, same
retry policy, same environment variable to point at a local or an external model.
They exist side by side to make the central claim checkable rather than rhetorical
— if the integration pattern is sound, the language and framework of the app layer
are an implementation detail. Run all three and compare the answers at `:8080`,
`:8081` and `:8082`.

This is the same shape as Nextcloud's `integration_openai`-style apps, where the
collaboration platform calls an AI backend over HTTP and stays agnostic about
where that backend lives.

## Endpoints

| Method | Path (PHP)     | Proxies to (AI)   | Purpose                                   |
|--------|----------------|-------------------|-------------------------------------------|
| GET    | `/api/health`  | `GET /health`     | Answers ok unconditionally; see below     |
| POST   | `/api/ingest`  | `POST /ingest`    | Chunk, embed and store a document         |
| POST   | `/api/query`   | `POST /query`     | Retrieve top-k chunks, answer with sources|

The Node app exposes the same three paths on `:8081`; the Symfony app exposes them
under `/api` on `:8082`, plus `/api/audit`. All three serve a minimal UI at `/`.

The AI service carries two further endpoints that the app surfaces do not proxy,
because they exist for an orchestrator rather than for a user:

| Method | Path (AI)  | Purpose                                                        |
|--------|------------|----------------------------------------------------------------|
| GET    | `/alive`   | Liveness. `async`, so it never queues behind inference           |
| GET    | `/ready`   | Readiness. Checks the model backend and free worker threads      |

`/health` is neither of those and never was: it is synchronous, so it competes
for the same threadpool as `/query`, and it reports ok whether or not anything
works. Pointing a liveness probe at it gets a healthy service killed — measured,
with numbers, in [`deploy/README.md`](deploy/README.md).

## Run it

### With Docker (recommended)

```bash
cp .env.example .env
# edit .env: set OPENAI_API_KEY for the external path,
# or switch AI_PROVIDER=local and point OPENAI_BASE_URL at your local server.

docker compose up --build
# PHP UI:      http://localhost:8080
# Node UI:     http://localhost:8081
# Symfony UI:  http://localhost:8082   (API key: dev-key)
# AI service:  http://localhost:8000/health
```

Then, from another shell:

```bash
scripts/smoke.sh          # health -> ingest sample doc -> ask a question
```

### Local dev (no Docker)

```bash
# terminal 1 — AI service
cd ai-service
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...        # or configure a local backend
uvicorn app.main:app --port 8000

# terminal 2 — PHP app
cd php-app
AI_SERVICE_URL=http://localhost:8000 php -S localhost:8080 -t public public/index.php

# terminal 3 — Node app (optional; the same surface in TypeScript)
cd node-app
npm install && npm run build
AI_SERVICE_URL=http://localhost:8000 npm start
```

## The TypeScript side

`node-app/` is a deliberate second implementation rather than a port for its own
sake. Three things are worth a look:

**The retry policy is identical, and tested.** Network errors and 5xx are retried
with 200 ms then 400 ms backoff; 4xx — including a 429 from an exhausted quota —
is surfaced immediately, because the same request would fail the same way.
`npm test` runs eleven behavioural tests against a throwaway HTTP server, using
`node:test` and `node:assert` so the app stays dependency-free.

**The REST boundary is validated, not cast.** A boundary is exactly where the
compiler's guarantees stop: it knows nothing about what FastAPI actually put on
the wire. Writing `await res.json() as HealthResponse` would keep the feeling of
type safety while discarding the substance, and the first schema change on the
Python side would surface as an undefined somewhere far away. So every response
passes through hand-written guards in `src/contracts.ts` and a violation raises a
`ContractError` naming the endpoint and the offending field. `tsconfig.json` runs
`strict` plus `noUncheckedIndexedAccess`, which is what makes skipping those
guards impossible rather than merely discouraged.

**No runtime dependencies.** Three routes and one static file do not justify a
framework; `node:http` and the built-in `fetch` cover it. The production image is
the Node base plus roughly 20 kB of compiled JavaScript.

## The Symfony side

`symfony-app/` is the third surface, and the only one that is not just the same
proxy in another language. Symfony and Doctrine buy something the stateless clients
cannot have, so the app uses it rather than re-implementing `php-app` with more
ceremony.

**An audit trail, because that is what a database is for here.** `php-app` and
`node-app` forget every request the moment they return it. In the regulated
settings this pattern keeps turning up in, you have to be able to say months later
which question was asked, which passages the answer was built from, and which model
produced it. A Doctrine entity records exactly that, and `/api/audit` reports two
numbers: the share of answers that cited nothing retrieved, and how many cited
something that was never retrieved at all.

The second one is the dangerous case, because a reader cannot see it — the citation
format is identical whether the id is real or invented. The check is the same one
the Python evaluation suite runs offline (`check_citations` in
`ai-service/eval/metrics.py`), ported to PHP and held in place by tests on both
sides. One definition of "grounded" enforced twice beats two definitions that drift.

**The first version of that metric was wrong, and running it is what showed that.**
It asked whether the *retriever* returned anything. Top-k is unconditional, so it
returns something for every query including the unanswerable ones — the number read
0 forever. The first live run scored `I do not know. [source#0]` as grounded: an
abstention, citing a chunk that does not exist, counted as a good answer. The metric
now looks at what the answer actually stood on, and the same query is recorded as
ungrounded with `source#0` flagged as invented.

**The write happens on an event, not in the request path.** The controller
dispatches `QueryAnswered`; a listener persists it. The caller already has a
correct answer by then, so a database hiccup is logged rather than turned into a
500. That is a deliberate trade-off and the code says so: if the audit trail had to
be transactional with the answer, the listener would be the wrong design.

**The retry policy is written out by hand, again.** Symfony ships
`RetryableHttpClient`, which would express it in configuration. Not using it is the
point — three surfaces exist to compare one behaviour across three idioms, and
hiding it inside framework config would remove the thing being compared.

**An API key on everything except `/api/health`**, via a custom authenticator that
compares with `hash_equals` (a plain `===` here leaks the key through response
timing) and fails closed when the configured key is empty. It doubles as the
firewall's entry point, so a request with no credentials gets JSON instead of
Symfony's HTML error page — an HTML 401 to a JSON client is a parse error dressed
up as an auth failure.

```bash
cd symfony-app
composer install
php bin/console doctrine:schema:create
vendor/bin/phpunit          # 33 tests, 69 assertions
```

The suite covers the retry policy against a mocked transport (the same eleven
behaviours `node-app` tests), the citation check against the same cases as its
Python twin, the Doctrine mapping and DQL against a real schema in in-memory SQLite
rather than a mocked EntityManager, and the full request path including the
firewall, the audit write and an invented citation being recorded.

## Does it actually stay grounded?

The system prompt promises two things: answer only from the retrieved context, and say
"I do not know" when the answer is not there. Claims like that are cheap. `ai-service/eval/`
is a labelled dataset and a harness that checks them — 8 answerable questions with the
source chunk labelled, 5 that the corpus genuinely cannot answer, and four metrics of
which three are deterministic set arithmetic rather than a second model's opinion.

```bash
cd ai-service
python -m unittest discover -s . -p "test_*.py"   # 31 metric tests, no service needed
python -m eval.run_eval --k 4                     # scorecard against a running service
```

Against a self-hosted `llama3.2:1b` on Ollama it holds at k=4 — every unanswerable
question refused, every citation grounded. At k=2 it does not: three invented citations
and three questions answered that should not have been, including a confident
*"the Kubernetes operator … is maintained by [OpenAI]"* for a project that has neither.
The failure mode is context starvation, not context overload.

The suite also found a real defect in this repo: `/ingest` was not idempotent, so
re-ingesting a document left duplicate chunks that crowded out genuinely different ones
in the top-k. Fixed, and recall@2 went from 0.750 to 1.000 as a result. Full write-up
including the limitations of the abstention metric: [ai-service/eval/README.md](ai-service/eval/README.md).

## Local vs external: one code path

Both OpenAI and self-hosted servers expose the same OpenAI-compatible HTTP API, so
`ai-service/app/rag.py` builds its embeddings and chat client the same way for
both. You switch with environment variables only:

| Variable          | External (OpenAI)             | Local (LocalAI / Ollama)              |
|-------------------|-------------------------------|---------------------------------------|
| `AI_PROVIDER`     | `openai`                      | `local`                               |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1`   | e.g. `http://host.docker.internal:11434/v1` |
| `OPENAI_API_KEY`  | your key                      | `not-needed`                          |
| `CHAT_MODEL`      | `gpt-4o-mini`                 | e.g. `llama3.2`                       |
| `EMBED_MODEL`     | `text-embedding-3-small`      | e.g. `nomic-embed-text`               |

## Design notes

- **REST boundary on purpose.** PHP does not import a model SDK. It calls HTTP.
  That keeps the PHP side small and lets the AI service be replaced, scaled or
  self-hosted without touching the app.
- **Graceful degradation.** `AiClient` retries transient failures (network errors
  and 5xx) with exponential backoff, but never retries 4xx. A restarting model
  backend does not become a user-facing error.
- **In-memory vector store** keeps the demo dependency-light. Because everything
  goes through LangChain's `VectorStore` interface, swapping in pgvector, Qdrant
  or Chroma is a one-line change.
- **Lazy engine init** means the container and `/health` come up without
  credentials; keys are only needed once a real embed/answer call happens. The
  flip side is that "up" and "able to answer" are different states, which is why
  `/ready` exists and `/health` is not a readiness signal.
- **Deployed, and measured under load.** [`deploy/`](deploy/) runs the whole
  system on Kubernetes with Terraform-managed workloads. The interesting part is
  not that it deploys: it is that the probe configuration most services ship with
  restarted this one three times in three minutes while nothing was wrong, and
  that each restart silently emptied the vector store while the pod stayed green.

## Not in scope

This is a focused demo, not a product. No auth, no persistence, no rate limiting,
single-tenant in-memory index. Those are deliberately left out to keep the
integration pattern legible.

## License

MIT. Built by [Gunter Kreck](https://www.linkedin.com/in/gunter-kreck/).
