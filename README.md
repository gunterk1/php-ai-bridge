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
┌──────────────────────┐  ┌──────────────────────┐      REST / JSON       ┌───────────────────────────┐
│  php-app  (PHP 8.3)  │  │ node-app (TS, Node22)│  ────────────────────▶ │  ai-service (FastAPI)     │
│  • front controller  │  │  • front controller  │   POST /ingest         │  • LangChain               │
│  • AiClient (curl,   │  │  • AiClient (fetch,  │   POST /query          │  • RecursiveCharacterSplit │
│    retries, backoff) │  │    same retry policy)│   GET  /health         │  • embeddings + vector DB  │
│  • minimal UI        │  │  • contract guards   │  ◀──────────────────── │  • RAG answer + sources    │
└──────────────────────┘  └──────────────────────┘                        └───────────────────────────┘
                                                      │ OpenAI-compatible API
                                          ┌───────────┴────────────┐
                                          ▼                        ▼
                                    OpenAI (external)      LocalAI / Ollama (local)
```

The app owns the product surface; the AI capability lives behind HTTP. The two
sides scale, deploy and fail independently.

**Two product surfaces, one boundary.** `php-app` and `node-app` are behaviour-
identical clients of the same service: same three endpoints, same retry policy,
same environment variable to point at a local or an external model. They exist
side by side to make the central claim checkable rather than rhetorical — if the
integration pattern is sound, the language of the app layer is an implementation
detail. Run both and compare the answers at `:8080` and `:8081`.

This is the same shape as Nextcloud's `integration_openai`-style apps, where the
collaboration platform calls an AI backend over HTTP and stays agnostic about
where that backend lives.

## Endpoints

| Method | Path (PHP)     | Proxies to (AI)   | Purpose                                   |
|--------|----------------|-------------------|-------------------------------------------|
| GET    | `/api/health`  | `GET /health`     | Liveness + which provider is configured   |
| POST   | `/api/ingest`  | `POST /ingest`    | Chunk, embed and store a document         |
| POST   | `/api/query`   | `POST /query`     | Retrieve top-k chunks, answer with sources|

The Node app exposes exactly the same three paths on `:8081`. `/` serves a
minimal UI on both.

## Run it

### With Docker (recommended)

```bash
cp .env.example .env
# edit .env: set OPENAI_API_KEY for the external path,
# or switch AI_PROVIDER=local and point OPENAI_BASE_URL at your local server.

docker compose up --build
# PHP UI:     http://localhost:8080
# Node UI:    http://localhost:8081
# AI service: http://localhost:8000/health
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
  credentials; keys are only needed once a real embed/answer call happens.

## Not in scope

This is a focused demo, not a product. No auth, no persistence, no rate limiting,
single-tenant in-memory index. Those are deliberately left out to keep the
integration pattern legible.

## License

MIT. Built by [Gunter Kreck](https://www.linkedin.com/in/gunter-kreck/).
