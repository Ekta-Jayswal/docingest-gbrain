# What this file does: sends one chat request to the LiteLLM proxy and returns its JSON answer.
# The proxy speaks the OpenAI chat format, so this works with the models your organization
# offers, not only Anthropic's. The AI steps (--llm) use it for extraction and for suggestions.
"""Chat client for the LiteLLM proxy (OpenAI-compatible chat completions).

Settings come from the environment (set them in .env.local):

  LITELLM_BASE_URL   the proxy address, including /v1
  LITELLM_API_KEY    the proxy key
  LLM_MODEL          the chat model name, default gpt-5-mini-ai
  LLM_PRICE_IN, LLM_PRICE_OUT   optional, USD per million tokens, for the cost estimate
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "gpt-5-mini-ai"


# Raised when a chat call fails. The AI steps catch this one type and record the reason.
class LLMError(Exception):
    """A chat call failed: the network, an HTTP error, or an answer that is not usable."""


# The chat model to use. LLM_MODEL overrides the default, so a model change needs no code edit.
def model_name() -> str:
    """The chat model name from LLM_MODEL, or the default."""
    return os.environ.get("LLM_MODEL") or DEFAULT_MODEL


# Prices for the cost estimate. The proxy does not report prices, so they are 0 unless set.
def prices() -> tuple[float, float]:
    """(input, output) USD per million tokens, from LLM_PRICE_IN and LLM_PRICE_OUT."""
    try:
        return (
            float(os.environ.get("LLM_PRICE_IN", 0) or 0),
            float(os.environ.get("LLM_PRICE_OUT", 0) or 0),
        )
    except ValueError:
        return 0.0, 0.0


# The proxy address and key from the environment, as a (base URL, key) pair.
def settings() -> tuple[str | None, str | None]:
    """The proxy base URL and key from LITELLM_BASE_URL and LITELLM_API_KEY."""
    return os.environ.get("LITELLM_BASE_URL"), os.environ.get("LITELLM_API_KEY")


# Makes chat requests to the proxy. Each request asks for an answer in a given JSON schema.
class ChatClient:
    """Makes chat requests to the proxy, asking for an answer in a given JSON schema."""

    # Store the proxy address, key, and model. Without an address and a key nothing can be called,
    # so this fails early.
    def __init__(self, base_url: str | None, api_key: str | None, model: str | None = None,
                 timeout: int = 300):  # fmt: skip
        # Without an address and a key there is nothing to call, so fail with a clear message.
        if not base_url or not api_key:
            raise LLMError("LITELLM_BASE_URL and LITELLM_API_KEY must be set in .env.local")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key
        self.model = model or model_name()
        self.timeout = timeout

    # Send one request and return (the answer text, token usage). Raises LLMError on failure.
    def complete(
        self, system: str, user: str, schema: dict, name: str = "answer"
    ) -> tuple[str, dict]:
        """One chat request. The answer must follow the JSON schema. Returns (text, usage)."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Ask for JSON that follows the schema, so the answer can be parsed without guessing.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            raise LLMError(f"API error {e.code}: {e.read(300).decode(errors='replace')}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"connection error: {e}") from e
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected reply: {str(data)[:200]}") from e
        usage = data.get("usage") or {}
        return text, {
            "input": int(usage.get("prompt_tokens") or 0),
            "output": int(usage.get("completion_tokens") or 0),
            "cache_read": 0,
        }
