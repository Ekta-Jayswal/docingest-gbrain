# What this file does: tests tables with no ID column. They are typed by what their columns say.
# Cover, change history and signature tables are read as facts about the document.

"""Tables without an ID column: typed by what their columns are, never by one document."""

from __future__ import annotations

import unittest

from tests.test_list_extraction import VOCAB, extract


# Build a markdown pipe table from a header and rows.
def table(header: list[str], *rows: list[str]) -> str:
    """A markdown pipe table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


# Map each name to the page, or shared entity, of one vocabulary type.
def by_type(res, vocab_type: str) -> dict[str, object]:
    """Name -> page or shared entity of one type."""
    gtype = VOCAB.gbrain_type(vocab_type)
    out = {p.title.split(": ", 1)[-1]: p for p in res.pages if p.type == gtype}
    out |= {g.name: g for g in res.globals if g.vocab_type == vocab_type}
    return out


# The document's own page, which is always the first page.
def doc(res):
    """The source document page."""
    return res.pages[0]


# Which tables become which entities, judged from their columns.
class TableTypesTest(unittest.TestCase):
    """Which tables become which entities."""

    # A Name column makes people. An Organisation column links each person to that organization.
    def test_people_table_with_organisation(self):
        md = "# Doc\n\n## Team\n\n" + table(
            ["Name", "Organisation", "Role"],
            ["Jane Doe", "Acme Ltd", "Project manager"],
            ["Raj Mehta", "Acme Ltd", "Tester"],
        )
        res = extract(md)
        people = by_type(res, "person")
        self.assertEqual(set(people), {"Jane Doe", "Raj Mehta"})
        acme = by_type(res, "organization")["Acme Ltd"].slug
        self.assertEqual(people["Jane Doe"].links.get("member_of"), [acme])
        self.assertEqual(people["Jane Doe"].attributes.get("role"), "Project manager")

    # A table of parties links the document to each organization, by its role (Client or Vendor).
    def test_parties_link_the_document(self):
        md = "# Doc\n\n## Parties\n\n" + table(
            ["Party", "Role"], ["Acme Ltd", "Client"], ["Build Co", "Vendor"]
        )
        res = extract(md)
        orgs = by_type(res, "organization")
        self.assertEqual(doc(res).links.get("has_client"), [orgs["Acme Ltd"].slug])
        self.assertEqual(doc(res).links.get("has_vendor"), [orgs["Build Co"].slug])

    # The subject column names the type of the rows: 'Phase' gives milestones, 'Connected system'
    # gives systems.
    def test_subject_column_names_the_type(self):
        md = "# Doc\n\n## Plan\n\n" + table(
            ["Phase", "Exit criteria"], ["Pilot", "Ten users"], ["Launch", "All users"]
        )
        self.assertEqual(set(by_type(extract(md), "milestone")), {"Pilot", "Launch"})
        md = "# Doc\n\n## Interfaces\n\n" + table(["Connected system", "Direction"], ["Ledger", "In"])
        self.assertEqual(set(by_type(extract(md), "system")), {"Ledger"})

    # A column that only numbers the rows is skipped, and the next column names the entities.
    def test_numbering_column_is_skipped(self):
        md = "# Doc\n\n## Schedule\n\n" + table(
            ["No.", "Deliverable", "Due"], ["1", "Plan", "May"], ["2", "Report", "June"]
        )
        self.assertEqual(set(by_type(extract(md), "deliverable")), {"Plan", "Report"})

    # A table about deliverables that already exist adds acceptance criteria for them, with no
    # duplicate deliverables.
    def test_table_about_known_items(self):
        md = (
            "# Doc\n\n## Deliverables\n\n"
            + table(["Deliverable", "Due"], ["Plan", "May"], ["Report", "June"])
            + "\n## Acceptance\n\n"
            + table(["Deliverable", "Acceptance criterion"], ["Report", "Signed by the client."])
        )
        res = extract(md)
        (ac,) = by_type(res, "acceptance_criterion").values()
        report = by_type(res, "deliverable")["Report"].slug
        self.assertEqual(ac.links.get("verifies"), [report])
        self.assertEqual(len(by_type(res, "deliverable")), 2)  # no duplicate deliverables

    # A column named for two types ('Question or risk') is ambiguous, so its rows are not typed.
    def test_header_naming_two_types_stays_text(self):
        md = "# Doc\n\n## Open items\n\n" + table(
            ["Question or risk", "Owner"], ["Is the API ready?", "Jane Doe"]
        )
        res = extract(md)
        self.assertEqual(by_type(res, "risk"), {})
        self.assertEqual(by_type(res, "open_question"), {})

    # A generic column (such as 'Data') names no type, so the table stays as text.
    def test_general_columns_name_nothing(self):
        md = "# Doc\n\n## Data\n\n" + table(["Data", "Source"], ["Payments", "Bank"])
        self.assertEqual(extract(md).tables[0].mapped_to, "text only")


# Cover tables, change history and signature tables.
class DocumentTablesTest(unittest.TestCase):
    """Cover tables, change history and signatures."""

    # A two-column table of labels such as 'Document ID' becomes document fields. A job title is
    # not a person.
    def test_cover_table_by_its_labels(self):
        md = "# Doc\n\n## Document Information\n\n" + table(
            ["Item", "Detail"],
            ["Document ID", "BRD-9"],
            ["Prepared by", "Jane Doe, Business Analyst"],
        )
        res = extract(md)
        self.assertEqual(doc(res).fields.get("document_id"), "BRD-9")
        self.assertEqual(set(by_type(res, "person")), {"Jane Doe"})  # a job title is no person

    # A change history with an author and a change column, but no version, is still recognised as
    # document control.
    def test_change_history_without_version_column(self):
        md = "# Doc\n\n## History\n\n" + table(
            ["Date", "Author", "What changed"], ["2026-01-02", "Jane Doe", "First draft"]
        )
        self.assertEqual(extract(md).tables[0].mapped_to, "document control")

    # In a signatures table, a person is approved only when their row has a date.
    def test_signatures_table_signed_when_dated(self):
        md = "# Doc\n\n## Signatures\n\n" + table(
            ["Party", "Name", "Title", "Date"],
            ["Acme Ltd", "Jane Doe", "CEO", "2026-01-02"],
            ["Build Co", "Raj Mehta", "CFO", ""],
        )
        res = extract(md)
        people = by_type(res, "person")
        self.assertEqual(people["Jane Doe"].links.get("approved"), [doc(res).slug])
        self.assertNotIn("approved", people["Raj Mehta"].links)
        acme = by_type(res, "organization")["Acme Ltd"].slug
        self.assertEqual(people["Jane Doe"].links.get("member_of"), [acme])

    # In a table with a signature column, a person is approved when the signature is filled in.
    def test_signature_column_filled_in(self):
        md = "# Doc\n\n## Approval\n\n" + table(
            ["Name", "Title", "Signature", "Date"],
            ["Jane Doe", "Director", "Jane Doe", "2026-01-02"],
            ["Raj Mehta", "Manager", "", ""],
        )
        res = extract(md)
        people = by_type(res, "person")
        self.assertIn("approved", people["Jane Doe"].links)
        self.assertNotIn("approved", people["Raj Mehta"].links)


# Titles, cover lines and signature blocks that are written as plain text.
class FrontMatterTest(unittest.TestCase):
    """Titles, cover lines and signature blocks written as plain text."""

    # A plain title line above a numbered heading becomes the document title, with the next line as
    # its subject.
    def test_title_from_plain_lines_before_a_numbered_heading(self):
        md = "Business Requirements Document: Payments\n\nAcme Ltd\n\n# 1. Document Control\n\ntext\n"
        self.assertEqual(extract(md).title, "Business Requirements Document: Payments")

    # A title phrase on a plain line takes the next plain line as its subject.
    def test_title_phrase_takes_the_next_line_as_subject(self):
        md = "Statement of Work\n\nWebsite Rebuild\n\n# Contents\n\ntext\n"
        self.assertEqual(extract(md).title, "Statement of Work: Website Rebuild")

    # A title heading followed by a sub-heading names the subject.
    def test_title_heading_with_a_sub_heading(self):
        md = "# STATEMENT OF WORK\n\n## Website Rebuild\n\nText.\n\n## 1. Purpose\n\ntext\n"
        self.assertEqual(extract(md).title, "STATEMENT OF WORK: Website Rebuild")

    # An ordinary title is used as it is.
    def test_ordinary_title_is_unchanged(self):
        md = "# Meeting notes\n\n## Items\n\ntext\n"
        self.assertEqual(extract(md).title, "Meeting notes")

    # Cover lines are read whether bold, bulleted or plain. A sentence in a numbered section is not
    # a cover line.
    def test_cover_lines_bold_bulleted_and_plain(self):
        md = (
            "# Plan\n\n**Contract No.:** C-77  \n- **Owner (product):** Jane Doe\n"
            "- Design lead: Raj Mehta\nAgency: Acme Ltd\nStatus: Under Review\n\n"
            "## 1. Purpose\n\nNotes: not a cover line.\n"
        )
        d = doc(extract(md))
        self.assertEqual(d.fields.get("document_id"), "C-77")
        self.assertEqual(d.fields.get("status"), "Under Review")
        self.assertNotIn("notes", d.fields)
        self.assertEqual(len(d.links.get("owned_by", [])), 1)
        self.assertEqual(len(d.links.get("has_client", [])), 1)
        people = by_type(extract(md), "person")
        self.assertEqual(set(people), {"Jane Doe", "Raj Mehta"})  # not "Under Review"

    # A signature block written as text makes the signer a person, linked to the organization in
    # its 'For ...' line.
    def test_signature_blocks_in_text(self):
        md = (
            "# Doc\n\n## 2. Signatures\n\n#### For the Department of Revenue\n\n"
            "Signature: /s/ Jo Doe\nName: Jo Doe\nTitle: Officer\nDate: 2026-01-02\n\n"
            "For Build Co.\nSignature:\nName: Raj Mehta\nDate:\n"
        )
        res = extract(md)
        people = by_type(res, "person")
        self.assertIn("approved", people["Jo Doe"].links)
        self.assertNotIn("approved", people["Raj Mehta"].links)
        self.assertEqual(people["Jo Doe"].attributes.get("job_title"), "Officer")
        orgs = by_type(res, "organization")
        self.assertEqual(people["Jo Doe"].links.get("member_of"), [orgs["Department of Revenue"].slug])

    # 'Calder Freight' and 'Calder Freight Ltd' are one organization.
    def test_organization_names_that_contain_each_other_are_one(self):
        md = (
            "# Doc\n\n**Client:** Calder Freight\n\n## 1. Parties\n\n"
            + table(["Party", "Role"], ["Calder Freight Ltd", "Client"])
        )
        self.assertEqual(len(by_type(extract(md), "organization")), 1)


if __name__ == "__main__":
    unittest.main()
