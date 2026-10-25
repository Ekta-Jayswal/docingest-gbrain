# What this file does: end-to-end tests of the embedding setup, run against a fake LiteLLM proxy.
# The ingest test also needs gbrain installed. Run with ./run_tests.sh.

"""End-to-end tests against a fake LiteLLM proxy. Run: ./run_tests.sh"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from docingest.ingestion import litellm_proxy
from docingest.commands import main
from docingest.evaluation.embedding_checks import _sample
from docingest.utils.gbrain_client import GBrain

from tests.helpers import fake_litellm_proxy

# The project root, two levels above this file.
ROOT = Path(__file__).resolve().parent.parent
# The sample documents folder, used as test input.
SAMPLES = ROOT / "sample_documents"


# Base class: starts the fake proxy and points the environment at it.
class FakeProxyTest(unittest.TestCase):
    """Starts the fake proxy once for every test in the class."""

    # Start the fake proxy on a free port, and point the LITELLM_* variables at it.
    @classmethod
    def setUpClass(cls):
        cls.server = fake_litellm_proxy.serve(0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = {k: os.environ.get(k) for k in ("LITELLM_BASE_URL", "LITELLM_API_KEY")}
        os.environ["LITELLM_BASE_URL"] = f"http://127.0.0.1:{cls.port}/v1"
        os.environ["LITELLM_API_KEY"] = "test"

    # Stop the proxy, and restore the environment variables it changed.
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# Checks that litellm_proxy.check reports good setups and explains bad ones.
class ProxyCheckTest(FakeProxyTest):
    """litellm_proxy.check reports good and bad setups."""

    # A model the proxy offers, at the right width, passes the check.
    def test_good_model(self):
        ok, msg = litellm_proxy.check("litellm:text-embed-3S-ai", 1536)
        self.assertTrue(ok, msg)

    # An unknown model fails, and the message lists the models that are on offer.
    def test_unknown_model_names_the_models_on_offer(self):
        ok, msg = litellm_proxy.check("litellm:nope", 1536)
        self.assertFalse(ok)
        self.assertIn("litellm:text-embed-3S-ai", msg)

    # A proxy that cannot be reached fails with a clear 'Cannot reach' message.
    def test_unreachable(self):
        os.environ["LITELLM_BASE_URL"] = "http://127.0.0.1:1/v1"
        try:
            ok, msg = litellm_proxy.check("litellm:text-embed-3S-ai", 1536)
        finally:
            os.environ["LITELLM_BASE_URL"] = f"http://127.0.0.1:{self.port}/v1"
        self.assertFalse(ok)
        self.assertIn("Cannot reach", msg)


# Full run: ingest a sample with embeddings switched on, then check the embedding report. Skipped
# when gbrain is not installed.
@unittest.skipUnless(
    shutil.which("gbrain") or (Path.home() / ".bun/bin/gbrain").exists(), "gbrain not installed"
)
class EndToEndTest(FakeProxyTest):
    """Ingest the samples into a new brain with embeddings, then check them."""

    # Ingesting with --embed embeds every chunk, and writes a passing embedding report.
    def test_ingest_embed_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            queries = ROOT / "tests" / "helpers" / "fake_proxy_search_questions.yaml"
            main(
                ["--brain", str(brain), "ingest", str(SAMPLES / "lighthouse_brd.md"), "--embed"]
                + ["--queries", str(queries)]
            )
            gb = GBrain(brain)
            self.assertTrue(gb.embeddings_enabled())
            state = gb.embedding_state()
            self.assertEqual(state["counts"]["pending"], 0)
            self.assertGreater(state["counts"]["embedded"], 0)
            report = (brain / "work" / "reports" / "embeddings-latest.json").read_text()
            self.assertIn('"status": "pass"', report)

    # A brain created without embeddings refuses --embed, and the error says why.
    def test_embed_refused_on_a_brain_without_embeddings(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            main(["--brain", str(brain), "ingest", str(SAMPLES / "ops_team_chat.txt")])
            with self.assertRaises(SystemExit) as ctx:
                main(
                    [
                        "--brain",
                        str(brain),
                        "ingest",
                        str(SAMPLES / "ops_team_chat.txt"),
                        "--embed",
                    ]
                )
            self.assertIn("embeddings off", str(ctx.exception))


# Checks for the small pure helper functions.
class SampleTest(unittest.TestCase):
    """Small pure functions."""

    # The sample picks evenly spaced items, the same way every time.
    def test_sample_is_even_and_repeatable(self):
        items = list(range(100))
        self.assertEqual(_sample(items, 5), [0, 20, 40, 60, 80])
        self.assertEqual(_sample(items[:3], 5), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
