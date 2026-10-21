"""End-to-end tests against a fake LiteLLM proxy. Run: ./run_tests.sh"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from docingest import proxy
from docingest.cli import main
from docingest.embedcheck import _sample
from docingest.gbrain import GBrain

from . import fake_litellm

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "inputs"


class FakeProxyTest(unittest.TestCase):
    """Starts the fake proxy once for every test in the class."""

    @classmethod
    def setUpClass(cls):
        cls.server = fake_litellm.serve(0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = {k: os.environ.get(k) for k in ("LITELLM_BASE_URL", "LITELLM_API_KEY")}
        os.environ["LITELLM_BASE_URL"] = f"http://127.0.0.1:{cls.port}/v1"
        os.environ["LITELLM_API_KEY"] = "test"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class ProxyCheckTest(FakeProxyTest):
    """proxy.check reports good and bad setups."""

    def test_good_model(self):
        ok, msg = proxy.check("litellm:text-embedding-3-small", 1536)
        self.assertTrue(ok, msg)

    def test_unknown_model_names_the_models_on_offer(self):
        ok, msg = proxy.check("litellm:nope", 1536)
        self.assertFalse(ok)
        self.assertIn("text-embedding-3-small", msg)

    def test_unreachable(self):
        os.environ["LITELLM_BASE_URL"] = "http://127.0.0.1:1/v1"
        try:
            ok, msg = proxy.check("litellm:text-embedding-3-small", 1536)
        finally:
            os.environ["LITELLM_BASE_URL"] = f"http://127.0.0.1:{self.port}/v1"
        self.assertFalse(ok)
        self.assertIn("Cannot reach", msg)


@unittest.skipUnless(
    shutil.which("gbrain") or (Path.home() / ".bun/bin/gbrain").exists(), "gbrain not installed"
)
class EndToEndTest(FakeProxyTest):
    """Ingest the samples into a new brain with embeddings, then check them."""

    def test_ingest_embed_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp) / "brain"
            queries = ROOT / "tests" / "queries_fake.yaml"
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


class SampleTest(unittest.TestCase):
    """Small pure functions."""

    def test_sample_is_even_and_repeatable(self):
        items = list(range(100))
        self.assertEqual(_sample(items, 5), [0, 20, 40, 60, 80])
        self.assertEqual(_sample(items[:3], 5), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
