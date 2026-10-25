# What this file does: tests the gap audit: what each section links to, the names that more than one
# section gives, the find result, and the page that is written. No model and no gbrain are needed.

"""The gap audit of one document: sections, items, repeated names and the page."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docingest.reporting import gap_audit
from docingest.ingestion.pipeline import document_manifest
from docingest.vocabulary.vocabulary_loader import Vocabulary
from tests.test_list_extraction import extract

VOCAB = Vocabulary()

# A short document: an objective list, a requirement table that names the objectives, a system named
# in the first and the last section, and a table kept as text.
DOC = """# Doc

## Project Objectives

1. Cut cycle time.
2. Go online.

## Business Requirements

| ID | Requirement | Objective |
|---|---|---|
| BR-01 | Apply online. | 1, 2 |

## Interfaces

| Connected system | Direction |
|---|---|
| PermitTrack | Both ways |

## Overview

Staff record every case in PermitTrack.

## Notes

| Thing | Detail |
|---|---|
| Alpha | Beta |
"""


# The audit of DOC, from the same manifest ingest writes.
def audit(md: str = DOC, find: str | None = None) -> dict:
    res = extract(md)
    manifest = document_manifest(res, VOCAB.gbrain_type("document_section"))
    shared = {g.slug: (g.vocab_type, g.name) for g in res.globals}
    return gap_audit.analyse(manifest, VOCAB, shared, find)


class GapAuditTest(unittest.TestCase):
    """What the audit reports for one document."""

    # Every section is listed, in reading order, with the items it names.
    def test_sections_are_listed_in_order_with_their_items(self):
        a = audit()
        labels = [r["label"].split(" (section ")[0] for r in a["sections"]]
        self.assertEqual(labels[:3], ["Doc", "Project Objectives", "Business Requirements"])
        objectives = next(r for r in a["sections"] if r["label"].startswith("Project Objectives"))
        self.assertEqual(len(objectives["items"]), 2)

    # A table kept as text is a gap. The Notes table has no ID and no known header, so it stays text.
    def test_gaps_name_the_text_table(self):
        a = audit()
        self.assertTrue(any("Thing, Detail" in g["detail"] and "kept as text" in g["detail"] for g in a["gaps"]))

    # An item with no typed link is a gap that names the item.
    def test_gaps_name_an_unlinked_item(self):
        a = audit(DOC + "\n## Assumptions\n\n- The team is staffed.\n")
        unlinked = [g["detail"] for g in a["gaps"] if "no typed link" in g["detail"]]
        self.assertEqual(unlinked, ["AS-1: The team is staffed. has no typed link"])

    # A requirement that names objectives shows them as typed links, in and out.
    def test_items_show_their_typed_links(self):
        a = audit()
        br = next(r for r in a["items"] if r["label"].startswith("BR-01"))
        self.assertTrue(br["typed"])
        objectives = sorted(t for v, t in br["out"] if v == "achieves")
        self.assertEqual(objectives, ["BO-1: Cut cycle time.", "BO-2: Go online."])

    # A name given in the first section and again in the last is listed with both.
    def test_a_name_in_the_first_and_last_section_is_repeated(self):
        md = (
            "# Doc\n\n## Ownership\n\n| Connected system | Direction |\n|---|---|\n"
            "| PermitTrack | Both ways |\n\nThe PermitTrack team owns the record.\n\n"
            "## Middle\n\nNothing here.\n\n## Closing\n\nPermitTrack is retired in May.\n"
        )
        a = audit(md)
        row = next(r for r in a["repeated"] if r["name"] == "PermitTrack")
        self.assertTrue(row["first"].startswith("Ownership"))
        self.assertTrue(row["last"].startswith("Closing"))

    # Find lists every section with the phrase, and says which is first and which is last.
    def test_find_lists_every_section_with_the_phrase(self):
        md = (
            "# Doc\n\n## First\n\nAcme pays on time.\n\n## Middle\n\nNothing.\n\n## Last\n\nThe acme account closes.\n"
        )
        f = audit(md, find="Acme")["found"]
        self.assertEqual(len(f["sections"]), 2)
        self.assertTrue(f["first"].startswith("First"))
        self.assertTrue(f["last"].startswith("Last"))
        self.assertIn("Acme pays", f["sections"][0]["snippet"])

    # Table pipes are shown as separators in a find snippet, not as raw markdown.
    def test_find_snippet_has_no_raw_pipes(self):
        md = "# Doc\n\n## Team\n\n" + "| Name | Role |\n|---|---|\n| Ravi Menon | Lead |\n"
        snippet = audit(md, find="Ravi Menon")["found"]["sections"][0]["snippet"]
        self.assertNotIn("|", snippet)

    # The page is written, has the graph and every section, and needs no network.
    def test_page_is_written_with_graph_and_sections(self):
        res = extract(DOC)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audit.html"
            a = gap_audit.write(res, VOCAB, out, find="PermitTrack")
            page = out.read_text()
        self.assertIn("<svg", page)
        self.assertIn("Every section", page)
        self.assertIn("Find: ", page)
        self.assertTrue(a["summary"]["sections"] >= 5)

    # The text summary names the totals and the gaps.
    def test_summary_lines_name_totals_and_gaps(self):
        lines = gap_audit.summary_lines(audit())
        self.assertIn("sections", lines[0])
        self.assertTrue(any(line.startswith("Gaps") for line in lines))


if __name__ == "__main__":
    unittest.main()
