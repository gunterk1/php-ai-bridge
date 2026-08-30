"""A deterministic stand-in for an OpenAI-compatible model server.

The point of the experiment is Kubernetes' behaviour under slow inference, not
the model's answer. A real provider's latency varies; this one does not, so the
same run produces the same numbers twice.

LATENCY_S controls how long a chat completion takes. Embeddings stay fast --
that asymmetry is realistic and keeps ingest out of the way of the measurement.
"""
import hashlib
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LATENCY_S = float(os.getenv("LATENCY_S", "20"))
EMBED_DIM = int(os.getenv("EMBED_DIM", "128"))


def _vector(item) -> list[float]:
    """A stable pseudo-embedding: same input in, same vector out, no model needed.

    The OpenAI embeddings API accepts either strings or pre-tokenised integer
    lists, and LangChain's client sends the latter by default. Both have to work
    or the stub only appears to be a drop-in replacement.
    """
    text = item if isinstance(item, str) else ",".join(str(t) for t in item)
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [digest[i % len(digest)] / 255.0 for i in range(EMBED_DIM)]
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        """/models is what a readiness check asks for: cheap, and it proves the
        server is answering rather than merely accepting connections."""
        if self.path.endswith("/models"):
            self._send({
                "object": "list",
                "data": [
                    {"id": "stub-chat", "object": "model"},
                    {"id": "stub-embed", "object": "model"},
                ],
            })
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path.endswith("/embeddings"):
            inputs = body.get("input") or []
            if isinstance(inputs, str) or (inputs and isinstance(inputs[0], int)):
                inputs = [inputs]
            self._send({
                "object": "list",
                "model": body.get("model", "stub-embed"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": _vector(t)}
                    for i, t in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            })
            return

        if self.path.endswith("/chat/completions"):
            time.sleep(LATENCY_S)
            self._send({
                "id": "stub",
                "object": "chat.completion",
                "model": body.get("model", "stub-chat"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "stub answer"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
            return

        self.send_error(404)

    def log_message(self, *_args) -> None:
        pass


if __name__ == "__main__":
    print(f"slow-provider listening on :9000, chat latency {LATENCY_S}s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
