"""A fake LiteLLM proxy for testing the embedding phase without credentials.

    python tests/fake_litellm.py [port]      # serves http://127.0.0.1:<port>/v1

Endpoints: GET /v1/models and POST /v1/embeddings (OpenAI format). Vectors are
deterministic: each word hashes to a few dimensions, so texts that share words get
similar vectors. Words are also folded through a small synonym table, so
"printer" and "print device" land close together, which lets tests check that
semantic search finds documents that share no keywords with the query.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "text-embedding-3-small"
SYNONYMS = {
    "print": "printer",
    "printing": "printer",
    "prints": "printer",
    "device": "printer",
    "cost": "price",
    "pricing": "price",
    "fee": "price",
    "fees": "price",
    "expensive": "price",
    "refund": "return",
    "returns": "return",
    "returned": "return",
    "reimbursement": "return",
    "angry": "complaint",
    "unhappy": "complaint",
    "complaints": "complaint",
    "delay": "late",
    "delayed": "late",
    "overdue": "late",
    "slow": "late",
}


def _words(text: str) -> list[str]:
    return [SYNONYMS.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower())]


def embed(text: str, dims: int = 1536) -> list[float]:
    """A unit vector built from word hashes (4 dimensions per word)."""
    vec = [0.0] * dims
    for w in _words(text):
        h = hashlib.sha256(w.encode()).digest()
        for i in range(4):
            vec[int.from_bytes(h[i * 4 : i * 4 + 4], "big") % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class Handler(BaseHTTPRequestHandler):
    """Serves the two OpenAI-style endpoints."""

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        """List models."""
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        """Embed texts."""
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if not self.path.rstrip("/").endswith("/embeddings"):
            return self._send(404, {"error": "not found"})
        inputs = body.get("input", [])
        inputs = [inputs] if isinstance(inputs, str) else inputs
        dims = int(body.get("dimensions") or 1536)
        data = [
            {"object": "embedding", "index": i, "embedding": embed(str(t), dims)}
            for i, t in enumerate(inputs)
        ]
        tokens = sum(len(str(t).split()) for t in inputs)
        self._send(
            200,
            {
                "object": "list",
                "data": data,
                "model": body.get("model", MODEL),
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            },
        )

    def log_message(self, *args) -> None:
        """Keep test output quiet."""
        pass


def serve(port: int = 4010) -> ThreadingHTTPServer:
    """Create the server (not yet serving)."""
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4010
    print(f"fake LiteLLM on http://127.0.0.1:{port}/v1", flush=True)
    serve(port).serve_forever()
