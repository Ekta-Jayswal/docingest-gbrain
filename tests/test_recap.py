# What this file does: tests the recap printed at the end of an ingest run. Each test writes a
# small manifest and verification result, then checks the lines the recap prints.

"""The end-of-run recap: built from a manifest, a verification report and the evaluation history."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from docingest.reporting import recap, run_history


# A minimal manifest for one document, with two sections and three pages, as the pipeline would
# write it.
def manifest(source: Path, **extra) -> dict:
    return {
        "title": source.stem,
        "kind": "brd",
        "source_path": str(source),
        "sha256": "0" * 64,
        "converter": "markdown",
        "warnings": [],
        "sections": [{"heading": "Scope"}, {"heading": "Risks"}],
        "tables": [],
        "pages": [
            {"type": "document_section", "links": {"section_of": ["doc"]}},
            {"type": "requirement", "links": {"mentions": ["a", "b"], "owned_by": ["c"]}},
            {"type": "requirement", "links": {"mentions": ["d"]}},
        ],
        **extra,
    }


# A minimal verification result for the document, with the given status and checks.
def verified(status: str, checks: list[dict]) -> dict:
    return {"key": "brd", "status": status, "chunks": 12, "links": 4, "checks": checks}


# Each test writes a manifest into a temporary work folder and checks the recap text.
class RecapTest(unittest.TestCase):
    # Create a work folder with one document's manifest, which includes an AI report.
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.src = self.work / "input" / "lighthouse_brd.md"
        self.src.parent.mkdir()
        self.src.write_text("# BRD\n")
        (self.work / "manifests").mkdir()
        (self.work / "manifests" / "brd.json").write_text(
            json.dumps(manifest(self.src, llm_report={"accepted": 3, "rejected": 1, "estimated_cost_usd": 0.02}))
        )

    # Remove the work folder after each test.
    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    # The result of an ingest run with one processed document. Any field can be overridden.
    def result(self, **over) -> dict:
        return {
            "processed": ["brd"],
            "failed": [],
            "deleted": [],
            "timings": {"brd": 3.2},
            "parents": {},
            "proposals_pending": [],
            "proposals_applied": [],
            "proposals_rolled_back": [],
            **over,
        }

    # The document block shows sections, pages, entities, chunks, links, rule and AI results, test
    # status and time taken.
    def test_document_block_has_counts_links_and_tests(self):
        checks = [
            {"name": "sections", "status": "pass", "detail": "ok"},
            {"name": "schema gaps", "status": "warn", "detail": "1 fallback(s)"},
            {"name": "rule confidence", "status": "pass", "detail": "All 2 sections fully covered"},
        ]
        out = recap.lines(self.work, self.result(), {"documents": [verified("warn", checks)], "summary": {
            "status": "warn", "pages": 3, "chunks": 12, "links": 4}}, None, "run-1")
        text = "\n".join(out)
        self.assertIn("Summary of run run-1", text)
        self.assertIn("lighthouse_brd.md (brd)", text)
        self.assertIn("content: 2 sections, 0 tables, 3 pages, 2 entities", text)
        self.assertIn("chunks: 12    links: 5 (mentions 3, owned_by 1, section_of 1)", text)
        self.assertIn("rules: All 2 sections fully covered", text)
        self.assertIn("AI: 3 accepted, 1 rejected (~$0.02)", text)
        self.assertIn("tests: WARN (2 pass, 1 warn, 0 fail)", text)
        self.assertIn("WARN schema gaps: 1 fallback(s)", text)
        self.assertIn("time: 3.2 s", text)
        self.assertIn("Totals: 1 document(s) ingested, 0 failed", text)
        self.assertIn("tests: WARN (3 pages, 12 chunks, 4 links verified)", text)

    # Without verification, the recap says so instead of showing counts.
    def test_without_verification_says_so(self):
        text = "\n".join(recap.lines(self.work, self.result(), None, None, "run-2"))
        self.assertIn("chunks: not counted (verification skipped)", text)
        self.assertIn("tests: not run (--no-verify)", text)
        self.assertNotIn("verified", text)

    # Failed files, deleted pages and schema decisions each get their own line.
    def test_failures_schema_and_deletions_are_listed(self):
        result = self.result(
            failed=[{"file": "/x/broken.pdf", "error": "conversion failed", "parent": None}],
            deleted=["old-page"],
            proposals_applied=["p1"],
            proposals_rolled_back=["p2"],
            proposals_pending=["p3"],
        )
        text = "\n".join(recap.lines(self.work, result, None, {"status": "pass"}, "run-3"))
        self.assertIn("Totals: 1 document(s) ingested, 1 failed", text)
        self.assertIn("embeddings: PASS", text)
        self.assertIn("deleted: 1 page(s) no longer produced by any document", text)
        self.assertIn("FAILED broken.pdf: conversion failed", text)
        self.assertIn("schema: the rules applied p1", text)
        self.assertIn("schema: rolled back p2", text)
        self.assertIn("schema: 1 proposal(s) wait for a reviewer: p3", text)

    # An evaluation made in this run is shown in place of an older saved one.
    def test_evaluation_from_this_run_is_shown_and_preferred(self):
        ran = {"document": "/answers/lighthouse_brd.md", "evaluated_at": "2026-10-22T09:00:00+0000", "summary": {
            "overall": 0.81, "classification": 1.0, "sections_f1": 0.9, "tables_accuracy": 0.8,
            "entities_f1": 0.79, "attributes_accuracy": 0.7, "links_f1": 0.65,
            "storage_pass_rate": 1.0, "retrieval_hit_rate": 0.6}}
        run_history.append(self.work, "evals", [{**ran, "evaluated_at": "2026-10-01T10:00:00+0000", "summary": {**ran["summary"], "overall": 0.1}}])
        text = "\n".join(recap.lines(self.work, self.result(), None, None, "run-5", [ran]))
        self.assertIn("eval (this run): overall 81%, classification 100%, sections F1 90%, tables 80%, "
                      "entities F1 79%, attributes 70%, links F1 65%, storage 100%, retrieval 60%", text)
        self.assertNotIn("overall 10%", text)

    # When evaluation ran but no answer key matched, the recap says so.
    def test_no_matching_answer_key_is_said(self):
        text = "\n".join(recap.lines(self.work, self.result(), None, None, "run-6", []))
        self.assertIn("eval: no answer key matches these documents", text)

    # When evaluation was skipped, the newest saved evaluation of the document is shown instead.
    def test_skipped_evaluation_is_silent_and_falls_back_to_history(self):
        run_history.append(self.work, "evals", [{"document": "/answers/lighthouse_brd.md", "evaluated_at": "2026-10-20T10:00:00+0000", "summary": {
            "overall": 0.9, "classification": 1.0, "sections_f1": 0.9, "tables_accuracy": 0.9,
            "entities_f1": 0.85, "attributes_accuracy": 0.8, "links_f1": 0.75,
            "storage_pass_rate": 1.0, "retrieval_hit_rate": 0.5}}])
        text = "\n".join(recap.lines(self.work, self.result(), None, None, "run-7", None))
        self.assertIn("eval (2026-10-20): overall 90%", text)
        self.assertNotIn("no answer key", text)

    # Of several saved evaluations of one file, the newest is shown. Other files are ignored.
    def test_latest_evaluation_of_the_same_file_is_shown(self):
        # A saved evaluation row with every score set to 1.0, except the overall score.
        def ev(day: str, overall: float, document: str = "/answers/lighthouse_brd.md") -> dict:
            scores = {k: 1.0 for k in ("classification", "sections_f1", "tables_accuracy", "entities_f1",
                                       "attributes_accuracy", "links_f1", "storage_pass_rate", "retrieval_hit_rate")}
            return {"document": document, "evaluated_at": day, "summary": {**scores, "overall": overall}}

        run_history.append(self.work, "evals", [
            ev("2026-10-01T10:00:00+0000", 0.8),
            ev("2026-10-20T10:00:00+0000", 0.9),
            ev("2026-10-21T10:00:00+0000", 0.1, document="/answers/other.md"),
        ])
        text = "\n".join(recap.lines(self.work, self.result(), None, None, "run-4", None))
        self.assertIn("eval (2026-10-20): overall 90%, classification 100%", text)
        self.assertNotIn("overall 10%", text)


if __name__ == "__main__":
    unittest.main()
