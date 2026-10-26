# What this file does: checks the LiteLLM proxy before embeddings are switched on.
# The proxy is an OpenAI-compatible server. Checking it first gives a clear fix message
# instead of a failure in the middle of an ingest.

"""Talk to the LiteLLM proxy (OpenAI-compatible) to check it before embedding."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


# Read the proxy address and key from LITELLM_BASE_URL and LITELLM_API_KEY.
def settings() -> tuple[str | None, str | None]:
    """The proxy base URL and API key from the environment."""
    return os.environ.get("LITELLM_BASE_URL"), os.environ.get("LITELLM_API_KEY")


# Make one HTTP call to the proxy. POST with a JSON body when body is given, otherwise GET.
# The key goes in a Bearer header when set.
def _call(url: str, key: str | None, body: dict | None = None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {key}"} if key else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


# Check the proxy in three steps: it is reachable, it lists the model, and a test embedding
# has the expected width. Returns (ok, message), and the message says how to fix a failure.
def check(model: str, dimensions: int) -> tuple[bool, str]:
    """List the proxy's models and embed one word. Returns (ok, message with the fix)."""
    base, key = settings()
    if not base:
        return False, (
            "LITELLM_BASE_URL is not set. Put LITELLM_BASE_URL and LITELLM_API_KEY in "
            ".env.local (see COMMANDS.md) or export them."
        )
    base = base.rstrip("/")
    name = model.split(":", 1)[-1]
    try:
        # Ask the proxy which models it offers. The model name may be given with a prefix such as
        # litellm:, so only the part after the colon is compared.
        models = [m.get("id", "").split(":", 1)[-1] for m in _call(f"{base}/models", key).get("data", [])]
    except urllib.error.HTTPError as e:
        hint = " Try the URL without (or with) a trailing /v1." if e.code == 404 else ""
        return False, f"{base}/models answered HTTP {e.code}.{hint}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return False, f"Cannot reach {base}: {e}"
    # The proxy is reachable but does not offer this model. Stop and list what it does offer.
    if models and name not in models:
        return False, (
            f"The proxy does not list model '{name}'. It offers: {', '.join(map(str, models))}. "
            "Pass the right name with --embedding-model litellm:<name>."
        )
    try:
        out = _call(
            f"{base}/embeddings", key, {"model": name, "input": "hello", "dimensions": dimensions}
        )
        # Embed one word and count the numbers in the vector, which must match the brain's width.
        got = len(out["data"][0]["embedding"])
    except (urllib.error.URLError, KeyError, IndexError, TimeoutError, json.JSONDecodeError) as e:
        return False, f"The embeddings call failed: {e}"
    if got != dimensions:
        return False, f"The model returned {got} dimensions, expected {dimensions}."
    return True, f"{base} offers {name}; a test embedding has {got} dimensions"
