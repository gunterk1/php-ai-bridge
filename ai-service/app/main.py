"""FastAPI wrapper exposing the RAG engine as a small REST service.

The PHP app is the only client. Keeping the AI capability behind a REST boundary
is the whole point of the demo: the product surface (PHP) and the AI backend
(Python) evolve and scale independently, exactly like Nextcloud's integration
apps talk to an AI service over HTTP.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .rag import RagEngine

app = FastAPI(title="php-ai-bridge AI service", version="0.1.0")

# The engine is created lazily so the container (and /health) come up even when
# no credentials are configured yet. Credentials are only needed once a real
# embed/answer call is made.
_engine: RagEngine | None = None


def get_engine() -> RagEngine:
    global _engine
    if _engine is None:
        _engine = RagEngine()
    return _engine


class IngestRequest(BaseModel):
    doc_id: str
    text: str


class QueryRequest(BaseModel):
    question: str
    k: int = 4


def _guard(fn, *args):
    """Run a backend call and translate provider failures into clean HTTP errors.

    The model backend can fail for reasons the app cannot fix (quota exhausted,
    backend down). We surface those as concise JSON instead of a raw traceback,
    and pick a status code the PHP client can act on: 429/503 are not retried as
    if they were transient app errors, so we do not hammer a backend that is out
    of quota.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: surface any backend failure
        message = str(exc)
        low = message.lower()
        if "insufficient_quota" in low or "rate limit" in low or "ratelimit" in low or " 429" in low:
            raise HTTPException(status_code=429, detail=f"AI backend quota/rate limit: {message}") from exc
        if "connection" in low or "refused" in low or "timeout" in low or "timed out" in low:
            raise HTTPException(status_code=503, detail=f"AI backend unreachable: {message}") from exc
        raise HTTPException(status_code=502, detail=f"AI backend error: {message}") from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": os.getenv("AI_PROVIDER", "openai")}


@app.post("/ingest")
def ingest(req: IngestRequest) -> dict:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    chunks = _guard(get_engine().ingest, req.doc_id, req.text)
    return {"doc_id": req.doc_id, "chunks": chunks}


@app.post("/query")
def query(req: QueryRequest) -> dict:
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")
    if req.k < 1 or req.k > 20:
        raise HTTPException(status_code=400, detail="k must be between 1 and 20")
    return _guard(get_engine().query, req.question, req.k)
