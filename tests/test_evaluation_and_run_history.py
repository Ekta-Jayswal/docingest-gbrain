# What this file does: tests the scoring helpers, the answer-key baseline, and that a run is
# recorded in the history and the dashboard. The end-to-end tests are skipped unless gbrain is
# installed.

"""Evaluation against the loan BRD answer key, and the run history and dashboard it feeds."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from docingest.reporting import dashboard_page

from docingest.evaluation import answer_key_evaluation

from docingest.reporting import run_history
from docingest.commands import main

# The project root, two levels above this file.
ROOT = Path(__file__).resolve().parent.parent
# The answer key used by the end-to-end tests (the loan origination BRD).
ANSWERS = ROOT / "answer_keys" / "loan_origination_brd.yaml"
# True when the gbrain program is installed, so the end-to-end tests can run.
HAS_GBRAIN = bool(shutil.which("gbrain") or (Path.home() / ".bun/bin/gbrain").exists())


# Checks for the scoring helpers.
class ScoringTest(unittest.TestCase):
    """The small scoring helpers."""

    # Precision, recall and F1 come out right for a known example.
    def test_prf(self):
        r = answer_key_evaluation.prf(right=8, produced=10, expected=16)
        self.assertAlmostEqual(r["precision"], 0.8)
        self.assertAlmostEqual(r["recall"], 0.5)
        self.assertAlmostEqual(r["f1"], 2 * 0.8 * 0.5 / 1.3)

    # Text matches regardless of case, and a short phrase matches a longer text that contains it.
    def test_same_text(self):
        self.assertTrue(answer_key_evaluation.same_text("Must", "must"))
        self.assertTrue(
            answer_key_evaluation.same_text("Loan Officers", "Loan Officers (front line)")
        )
        self.assertFalse(answer_key_evaluation.same_text("Must", "Should"))


# Full runs: ingest a real document into a throwaway brain. Skipped when gbrain is not installed.
@unittest.skipUnless(HAS_GBRAIN, "gbrain not installed")
class EndToEndTest(unittest.TestCase):
    """Ingest with a project, evaluate, and build the page."""

    # Create a temporary folder and a brain path for each test.
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.brain = self.tmp / "brain"

    # Remove the temporary folder after each test.
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # Scoring the loan BRD must reach the agreed floors. A failure means a change made extraction
    # worse.
    def test_baseline_scores_do_not_regress(self):
        r = answer_key_evaluation.run(ANSWERS, self.tmp / "work")
        s = r["summary"]
        # Floors from the latest milestone (M12, links from types); raise them as it improves.
        self.assertEqual(s["classification"], 1.0)
        self.assertGreaterEqual(r["stages"]["entities"]["precision"], 0.99)
        self.assertGreaterEqual(r["stages"]["entities"]["recall"], 0.8)
        self.assertGreaterEqual(s["tables_accuracy"], 0.95)
        self.assertGreaterEqual(s["attributes_accuracy"], 0.85)  # step automation needs the legend
        self.assertEqual(s["storage_pass_rate"], 1.0)
        self.assertGreaterEqual(s["sections_f1"], 0.9)
        self.assertGreaterEqual(s["retrieval_hit_rate"], 0.95)
        self.assertGreaterEqual(r["stages"]["links"]["precision"], 0.99)
        self.assertGreaterEqual(r["stages"]["links"]["recall"], 0.75)
        self.assertTrue(r["stages"]["document"]["title_found"])
        self.assertGreaterEqual(s["overall"], 0.9)
        self.assertIn("## loan_origination_brd", answer_key_evaluation.render(r))

    # Ingesting with a project name records one run and one document in the history, and builds the
    # dashboard page.
    def test_ingest_records_the_run_and_builds_the_page(self):
        doc = ANSWERS.with_suffix(".md")
        main(["--brain", str(self.brain), "ingest", str(doc), "--project", "dlos", "--team", "QA"])
        work = self.brain / "work"
        runs, docs = run_history.read(work, "runs"), run_history.read(work, "documents")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["project"], "dlos")
        self.assertEqual(docs[0]["extension"], "md")
        self.assertEqual(docs[0]["kind"], "brd")
        self.assertEqual(len(docs[0]["tables"]), 13)
        self.assertGreater(docs[0]["chunks"], 0)
        page = (work / "reports" / "dashboard.html").read_text()
        self.assertIn("loan_origination_brd.md", page)
        self.assertIn("dlos", page)
        self.assertIn("docingest run history", dashboard_page.render(work))


if __name__ == "__main__":
    unittest.main()
