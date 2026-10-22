# What this file does: tests the chat client that the --llm steps use. It runs against the fake
# LiteLLM proxy, so it needs no keys or network.
"""Chat client for the LiteLLM proxy: a reply comes back as text with its token counts."""

from __future__ import annotations

import os
import unittest

from docingest.utils import llm_client
from tests.test_embeddings import FakeProxyTest


# Checks for the chat client against the fake proxy.
class ChatClientTest(FakeProxyTest):
    # Build a client that points at the fake proxy started by the base class.
    def client(self) -> llm_client.ChatClient:
        return llm_client.ChatClient(f"http://127.0.0.1:{self.port}/v1", "test", "gpt-5-mini-ai")

    # A chat request returns the reply text and the token counts the proxy reported.
    def test_reply_text_and_usage(self):
        text, usage = self.client().complete("system", "user", {"type": "object"}, "answer")
        self.assertEqual(text, '{"ok": true, "model": "gpt-5-mini-ai"}')
        self.assertEqual(usage, {"input": 12, "output": 7, "cache_read": 0})

    # An unreachable proxy raises the one error type the callers catch, not a raw network error.
    def test_unreachable_is_an_llm_error(self):
        client = llm_client.ChatClient("http://127.0.0.1:1/v1", "test")
        with self.assertRaises(llm_client.LLMError):
            client.complete("system", "user", {"type": "object"})

    # Without an address and a key, the client refuses to start, with a message that says what to
    # set.
    def test_missing_settings_refused(self):
        with self.assertRaises(llm_client.LLMError) as ctx:
            llm_client.ChatClient(None, None)
        self.assertIn("LITELLM_BASE_URL", str(ctx.exception))

    # The model comes from LLM_MODEL when it is set, otherwise from the default.
    def test_model_from_environment(self):
        saved = os.environ.pop("LLM_MODEL", None)
        try:
            self.assertEqual(llm_client.model_name(), "gpt-5-mini-ai")
            os.environ["LLM_MODEL"] = "gpt-4o-mini-ai"
            self.assertEqual(llm_client.model_name(), "gpt-4o-mini-ai")
        finally:
            os.environ.pop("LLM_MODEL", None)
            if saved is not None:
                os.environ["LLM_MODEL"] = saved


if __name__ == "__main__":
    unittest.main()
