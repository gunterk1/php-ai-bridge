"""FastAPI wrapper exposing the RAG engine as a small REST service.

The PHP app is the only client. Keeping the AI capability behind a REST boundary
is the whole point of the demo: the product surface (PHP) and the AI backend
(Python) evolve and scale independently, exactly like Nextcloud's integration
apps talk to an AI service over HTTP.
"""

from __future__ import annotations

import os

import anyio.to_thread
import httpx
from fastapi import FastAPI, HTTPException, Response
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


def _free_worker_threads() -> int:
    """How many slots are left in the threadpool that serves sync endpoints.

    anyio caps it (40 by default) and every synchronous endpoint -- /query,
    /ingest, and /health -- draws from the same pool. Once it is empty, further
    sync requests queue, and the queue is invisible from outside unless
    something reports it.
    """
    try:
        limiter = anyio.to_thread.current_default_thread_limiter()
    except RuntimeError:  # no running event loop
        return -1
    return int(limiter.total_tokens - limiter.borrowed_tokens)


async def _backend_reachable() -> tuple[bool, str]:
    """Ask the configured model server whether it is there, briefly."""
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            await client.get(f"{base_url}/models")
        return True, "reachable"
    except httpx.HTTPError as exc:
        return False, f"unreachable: {type(exc).__name__}"


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
    """Kept for compatibility with docker-compose and the three app surfaces.

    Note what it is not: it is a synchronous endpoint, so Starlette runs it in
    the same bounded threadpool that serves /query. Under enough concurrent
    inference it waits for a work thread like everything else. Pointing a
    liveness probe at it makes the probe a queue-depth measurement -- see
    /alive.
    """
    return {"status": "ok", "provider": os.getenv("AI_PROVIDER", "openai")}


@app.get("/alive")
async def alive() -> dict:
    """Liveness: is this process still running its event loop?

    Declared async on purpose. Starlette runs async endpoints on the event loop
    instead of handing them to the threadpool, so this answers immediately no
    matter how many completions are in flight. A liveness probe must not share
    a resource with the work it is supposed to be observing -- otherwise a busy
    pod is indistinguishable from a dead one, and the kubelet resolves that
    ambiguity by killing it.
    """
    return {"status": "alive"}


@app.get("/ready")
async def ready(response: Response) -> dict:
    """Readiness: can this pod serve a request right now?

    Two things have to hold, and /health checks neither. The model backend has
    to be reachable, and there has to be a free work thread to run the call in.
    Answering ok unconditionally means traffic is routed to a pod that will 502,
    which is exactly the failure readiness exists to prevent.

    Failing readiness is cheap: the endpoint leaves the service, in-flight work
    finishes, and it comes back. Failing liveness is not: the pod is killed.
    """
    free_threads = _free_worker_threads()
    backend_ok, detail = await _backend_reachable()

    ok = backend_ok and free_threads > 0
    if not ok:
        response.status_code = 503

    return {
        "ready": ok,
        "backend": detail,
        "free_worker_threads": free_threads,
    }


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
