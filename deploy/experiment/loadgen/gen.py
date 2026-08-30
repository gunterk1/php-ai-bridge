"""Sustained load against the AI service, run from inside the cluster.

Driving this through `kubectl port-forward` from a laptop does not work, and the
failure is instructive: the forward dies together with the pod it points at, so
every worker then spins on a dead socket and the request count measures the
retry loop rather than the service. Talking to the Service from inside the
cluster keeps the endpoint stable across a restart -- which is the whole event
being measured.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

BASE = os.getenv("TARGET", "http://ai-service:8000")
CONCURRENCY = int(os.getenv("CONCURRENCY", "60"))
DURATION = int(os.getenv("DURATION_S", "150"))
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_S", "120"))
# Without a pause after a connection error, a dead endpoint is hit thousands of
# times a second and the totals stop meaning anything.
BACKOFF_S = float(os.getenv("BACKOFF_S", "1"))


EMPTY_STORE_ANSWER = "No documents have been ingested yet."


def post(path: str, payload: dict, timeout: float):
    """Returns (latency, error, body). error is None when the call succeeded."""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read()
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        return time.monotonic() - started, None, body
    except urllib.error.HTTPError as exc:
        return time.monotonic() - started, "HTTP " + str(exc.code), {}
    except Exception as exc:
        return time.monotonic() - started, type(exc).__name__, {}


def ingest(timeout: float = 60) -> bool:
    _, err, _ = post("/ingest", {
        "doc_id": "probe-note",
        "text": ("A liveness probe that queues behind the work it observes is "
                 "not a health check. Kubernetes restarts pods whose liveness "
                 "probe fails; a busy pod is not a dead pod."),
    }, timeout=timeout)
    return err is None


def main() -> None:
    for _ in range(60):
        if ingest():
            break
        time.sleep(1)
    else:
        print(json.dumps({"error": "could not ingest"}))
        return

    stop = threading.Event()
    lock = threading.Lock()
    ok_latencies = []
    failures = []
    # A restart wipes the in-memory vector store. The service then answers every
    # question with EMPTY_STORE_ANSWER: HTTP 200, no error, no LLM call, and no
    # signal anywhere that the index is gone. Counting these separately is the
    # only way the run distinguishes "served an answer" from "served nothing and
    # said it was fine".
    empty_store = []
    reingest_lock = threading.Lock()
    reingests = []

    def worker() -> None:
        while not stop.is_set():
            latency, err, body = post("/query", {"question": "what restarts a pod?", "k": 2},
                                      timeout=TIMEOUT)
            if err is not None:
                with lock:
                    failures.append(err)
                # The endpoint may be gone for a while; do not busy-loop on it.
                stop.wait(BACKOFF_S)
                continue

            if body.get("answer") == EMPTY_STORE_ANSWER:
                with lock:
                    empty_store.append(latency)
                # Put the index back so the run keeps producing real load, and
                # record that it had to be done.
                if reingest_lock.acquire(blocking=False):
                    try:
                        if ingest():
                            with lock:
                                reingests.append(time.monotonic())
                    finally:
                        reingest_lock.release()
                continue

            with lock:
                ok_latencies.append(latency)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(CONCURRENCY)]
    started = time.monotonic()
    for t in threads:
        t.start()
    stop.wait(DURATION)
    stop.set()
    for t in threads:
        t.join(timeout=TIMEOUT + 5)
    elapsed = time.monotonic() - started

    with lock:
        succeeded, failed = len(ok_latencies), len(failures)
        kinds = {k: failures.count(k) for k in sorted(set(failures))}
        latencies = sorted(ok_latencies)
        served_nothing = len(empty_store)
        reingest_count = len(reingests)

    print(json.dumps({
        "elapsed_s": round(elapsed, 1),
        "concurrency": CONCURRENCY,
        "answers_grounded": succeeded,
        "answers_from_empty_store": served_nothing,
        "reingests_needed": reingest_count,
        "requests_failed": failed,
        "failure_kinds": kinds,
        "latency_s": {
            "min": round(latencies[0], 2) if latencies else None,
            "median": round(latencies[len(latencies) // 2], 2) if latencies else None,
            "max": round(latencies[-1], 2) if latencies else None,
        },
    }))


if __name__ == "__main__":
    main()
