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
     ▼
┌──────────────────────┐        REST / JSON        ┌───────────────────────────┐
│  php-app  (PHP 8.3)  │  ───────────────────────▶ │  ai-service (FastAPI)     │
│  • front controller  │   POST /ingest            │  • LangChain               │
│  • AiClient (curl,   │   POST /query             │  • RecursiveCharacterSplit │
│    retries, backoff) │   GET  /health            │  • embeddings + vector DB  │
│  • minimal UI        │  ◀─────────────────────── │  • RAG answer + sources    │
└──────────────────────┘                           └───────────────────────────┘
                                                      │ OpenAI-compatible API
                                          ┌───────────┴────────────┐
                                          ▼                        ▼
                                    OpenAI (external)      LocalAI / Ollama (local)
```

PHP owns the product surface; the AI capability lives behind HTTP. The two sides
scale, deploy and fail independently. This is the same shape as Nextcloud's
`integration_openai`-style apps, where the collaboration platform calls an AI
backend over HTTP and stays agnostic about where that backend lives.

## Endpoints

| Method | Path (PHP)     | Proxies to (AI)   | Purpose                                   |
|--------|----------------|-------------------|-------------------------------------------|
| GET    | `/api/health`  | `GET /health`     | Liveness + which provider is configured   |
| POST   | `/api/ingest`  | `POST /ingest`    | Chunk, embed and store a document         |
| POST   | `/api/query`   | `POST /query`     | Retrieve top-k chunks, answer with sources|

`/` serves a minimal UI to try both steps in the browser.

## Run it

### With Docker (recommended)

```bash
cp .env.example .env
# edit .env: set OPENAI_API_KEY for the external path,
# or switch AI_PROVIDER=local and point OPENAI_BASE_URL at your local server.

docker compose up --build
# UI:         http://localhost:8080
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
```

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
