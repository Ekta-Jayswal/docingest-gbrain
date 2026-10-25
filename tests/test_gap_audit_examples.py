# What this file does: tests the gap audit on the loan origination BRD, one of the example documents.
# It checks the sections, the links, the names that repeat across sections, and the find result
# against what the document says. The document is converted and read in memory; no model or gbrain.

"""The gap audit of the loan origination BRD: links, sections and names, checked against the text."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from docingest.ingestion.convert_to_markdown import convert
from docingest.ingestion.extract_with_rules import Extractor
from docingest.reporting import gap_audit
from docingest.vocabulary.vocabulary_loader import Vocabulary

LOAN = Path(__file__).resolve().parents[1] / "answer_keys" / "loan_origination_brd.md"


# Extract the loan BRD once for all the tests, and build its audit with a credit bureau find.
class LoanAuditTest(unittest.TestCase):
    """What the audit says about the loan origination BRD."""

    @classmethod
    def setUpClass(cls):
        vocab = Vocabulary()
        with tempfile.TemporaryDirectory() as tmp:
            conv, reference = convert(LOAN, Path(tmp))
            x = Extractor(vocab)
            res = x.extract(LOAN, conv, reference, "brd", "loan-origination-brd", "")
        cls.manifest, cls.a = gap_audit.build(res, vocab, find="credit bureau")
        cls.sections = {r["label"].split(" (section ")[0]: r for r in cls.a["sections"]}
        cls.items = {r["label"].split(":")[0]: r for r in cls.a["items"]}
        cls.repeated = {r["name"]: r for r in cls.a["repeated"]}

    # The document has 25 sections, and every table became entities or document facts, none as text.
    def test_counts_match_the_document(self):
        s = self.a["summary"]
        self.assertEqual(s["sections"], 25)
        self.assertEqual(s["tables"], 13)
        self.assertEqual(s["tables_text"], 0)
        self.assertEqual(s["items"], 63)

    # The title section ("Business Requirements Document") names nothing: its sub-sections do, and a
    # title does not name the items of the whole document.
    def test_title_section_names_no_items(self):
        self.assertEqual(self.sections["Business Requirements Document"]["items"], [])

    # The objectives are defined in section 3.2, and the requirements that name them link to them.
    def test_objectives_are_named_in_their_section_and_linked(self):
        objectives = self.sections["3.2 Business Objectives"]
        self.assertEqual(objectives["typed"], 5)
        bo1 = self.items["BO-1"]
        self.assertEqual(bo1["type"], "business objective")
        self.assertEqual(bo1["sections"], [
            "3.2 Business Objectives (section 9)",
            "7.1 Applicant Experience (section 18)",
            "7.2 Processing & Decisioning (section 19)",
        ])

    # BO-1 is supported by the five requirements that name "(BO-1)" in their text.
    def test_requirements_link_to_the_objective_they_name(self):
        incoming = sorted(t.split(":")[0] for v, t in self.items["BO-1"]["in"] if v == "achieves")
        self.assertEqual(incoming, ["FR-01", "FR-05", "FR-07", "FR-08", "FR-10"])

    # FR-07 links to BO-1 and to the credit bureau system, which is a shared system.
    def test_a_requirement_links_to_objective_and_system(self):
        fr7 = self.items["FR-07"]
        self.assertEqual(fr7["sections"], ["7.2 Processing & Decisioning (section 19)"])
        self.assertIn(("achieves", "BO-1: Reduce application-to-decision cycle time"), fr7["out"])
        self.assertIn(("uses_system", "credit bureau"), fr7["out"])

    # The non-functional, KPI and risk tables have no objective or system column, so none of their
    # items has a typed link. The audit reports them as gaps.
    def test_tables_without_link_columns_are_gaps(self):
        for name in ("8. Non-Functional Requirements", "9. Success Metrics & Acceptance Criteria", "10. Key Business Risks"):
            with self.subTest(section=name):
                row = self.sections[name]
                self.assertTrue(row["items"])
                self.assertEqual(row["typed"], 0)
                self.assertIn(f"{len(row['items'])} item(s) with no typed link", row["flags"])
        self.assertEqual(self.items["NFR-02"]["typed"], False)
        self.assertEqual(self.items["KPI-1"]["typed"], False)

    # The executive summary is prose with no item in it, so the audit reports it.
    def test_executive_summary_is_reported_as_prose(self):
        self.assertIn("prose with no items", self.sections["2. Executive Summary"]["flags"])

    # The credit bureau is named in the executive summary and again in processing; both are listed as
    # its first and last sections, and it is counted in all four sections where the text names it.
    def test_credit_bureau_first_and_last_section(self):
        row = self.repeated["credit bureau"]
        self.assertEqual(row["kind"], "system")
        self.assertEqual(row["first"], "2. Executive Summary (section 6)")
        self.assertEqual(row["last"], "7.2 Processing & Decisioning (section 19)")
        self.assertEqual(len(row["sections"]), 4)

    # The find result for the same phrase lists those sections, and the requirement whose title holds it.
    def test_find_lists_every_section_and_the_item_with_the_phrase(self):
        f = self.a["found"]
        self.assertEqual(f["first"], "2. Executive Summary (section 6)")
        self.assertEqual(f["last"], "7.2 Processing & Decisioning (section 19)")
        self.assertEqual(len(f["sections"]), 4)
        self.assertTrue(any(t.startswith("FR-07") for t in f["items"]))

    # The named people in the approvals and the sign-off are named in both sections.
    def test_approvers_are_named_in_approvals_and_sign_off(self):
        row = self.repeated["D. Marchetti"]
        self.assertEqual(row["kind"], "person")
        self.assertEqual(row["first"], "1.2 Approvals (section 5)")
        self.assertEqual(row["last"], "12. Sign-off (section 25)")

    # The page for this document has its graph, its sections, and the find result.
    def test_page_has_graph_sections_and_find(self):
        page = gap_audit.render_page([(self.manifest, self.a)])
        self.assertIn("<svg", page)
        self.assertIn("Every section", page)
        self.assertIn("Find: “credit bureau”", page)
        self.assertIn("Gap audit: Business Requirements Document: Digital Loan Origination System", page)

    # Two documents on one page: each has its own section, and the page lists both at the top.
    def test_two_documents_share_one_page(self):
        page = gap_audit.render_page([(self.manifest, self.a), (self.manifest, self.a)])
        self.assertEqual(page.count('<section class="doc"'), 2)
        self.assertIn("<nav>Documents:", page)


if __name__ == "__main__":
    unittest.main()
