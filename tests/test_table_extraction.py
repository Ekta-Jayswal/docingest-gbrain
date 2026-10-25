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

    # A column named for two types ('Question or risk') is read row by row: a row that asks
    # something is an open question, any other row is a risk.
    def test_question_or_risk_header_is_read_row_by_row(self):
        md = "# Doc\n\n## Open items\n\n" + table(
            ["Question or risk", "Owner"],
            ["Is the API ready?", "Jane Doe"],
            ["Pharmacists may cancel refills.", "Raj Patel"],
        )
        res = extract(md)
        self.assertEqual(list(by_type(res, "open_question")), ["Is the API ready?"])
        self.assertEqual(list(by_type(res, "risk")), ["Pharmacists may cancel refills."])

    # A generic column (such as 'Data') names no type, so the table stays as text.
    def test_general_columns_name_nothing(self):
        md = "# Doc\n\n## Data\n\n" + table(["Data", "Source"], ["Payments", "Bank"])
        self.assertEqual(extract(md).tables[0].mapped_to, "text only")


# Links between a section and its items, and between items, that the document does not print as IDs.
class SectionAndRangeLinksTest(unittest.TestCase):
    """Section mentions for items without IDs, numbered objectives and ID ranges."""

    # An objective list has no printed IDs, yet its section links to every objective.
    def test_section_mentions_items_defined_in_it(self):
        md = "# Doc\n\n## Project Objectives\n\n1. Cut cycle time.\n2. Go online.\n"
        res = extract(md)
        section = next(p for p in res.pages if p.fields.get("heading") == "Project Objectives")
        objectives = {p.slug for p in res.pages if p.type == VOCAB.gbrain_type("business_objective")}
        self.assertEqual(len(objectives), 2)
        self.assertEqual(set(section.links.get("mentions_item", [])), objectives)

    # "1, 2" in an Objective column means the first and second numbered objectives.
    def test_objective_numbers_link_to_objectives(self):
        md = (
            "# Doc\n\n## Project Objectives\n\n1. Cut cycle time.\n2. Go online.\n\n"
            "## Business Requirements\n\n"
            + table(["ID", "Requirement", "Objective"], ["BR-01", "Apply online.", "1, 2"])
        )
        res = extract(md)
        (br,) = by_type(res, "business_requirement").values()
        objectives = [p.slug for p in res.pages if p.type == VOCAB.gbrain_type("business_objective")]
        self.assertEqual(sorted(br.links.get("achieves", [])), sorted(objectives))

    # A number that has no objective links to nothing.
    def test_objective_number_out_of_range_is_ignored(self):
        md = (
            "# Doc\n\n## Project Objectives\n\n1. Cut cycle time.\n\n"
            "## Business Requirements\n\n"
            + table(["ID", "Requirement", "Objective"], ["BR-01", "Apply online.", "3"])
        )
        (br,) = by_type(extract(md), "business_requirement").values()
        self.assertNotIn("achieves", br.links)

    # "US-1 to US-3" names US-2 as well.
    def test_id_range_is_expanded(self):
        from docingest.ingestion.extract_with_rules import ids_in

        self.assertEqual(ids_in("US-1 to US-3 and FR-2"), ["US-1", "US-3", "FR-2", "US-2"])
        self.assertEqual(ids_in("US-1 to 4"), ["US-1", "US-2", "US-3", "US-4"])
        self.assertEqual(ids_in("2026-10 to 2026-12"), [])  # dates are not IDs


# Structure the rules read from headings, plus links that follow from other links.
class StructureLinksTest(unittest.TestCase):
    """Sub-sections, headings that type a table, glossary terms, aliases and inherited objectives."""

    # The page of the section with this heading.
    def section(self, res, heading: str):
        return next(p for p in res.pages if p.fields.get("heading") == heading)

    # A heading under a heading links to it, and a container heading names its sub-sections' items.
    def test_container_section_names_items_of_its_subsections(self):
        md = (
            "# Doc\n\n# Functional Requirements\n\nGrouped by user.\n\n"
            "## Applicant\n\n- FR-1 The system shall let applicants apply.\n\n"
            "## Examiner\n\n- FR-2 The system shall queue reviews.\n"
        )
        res = extract(md)
        parent, child = self.section(res, "Functional Requirements"), self.section(res, "Applicant")
        self.assertEqual(child.links["subsection_of"], [parent.slug])
        self.assertEqual(len(parent.links["mentions_item"]), 2)

    # A section that has items of its own does not also take its sub-sections' items.
    def test_section_with_its_own_items_is_not_a_container(self):
        md = (
            "# Doc\n\n# Requirements\n\n- FR-1 The system shall do a.\n\n"
            "## Extra\n\n- FR-2 The system shall do b.\n"
        )
        res = extract(md)
        self.assertEqual(len(self.section(res, "Requirements").links["mentions_item"]), 1)

    # A table whose first column is generic is typed by a heading that names one type.
    def test_heading_types_a_table_with_a_generic_first_column(self):
        md = "# Doc\n\n## Cost-Benefit Analysis\n\n" + table(
            ["Item", "Type", "Amount"], ["Licence", "Cost", "180,000"], ["Re-keying saved", "Benefit", "95,000"]
        )
        res = extract(md)
        self.assertEqual(sorted(by_type(res, "budget_item")), ["Licence", "Re-keying saved"])
        md = "# Doc\n\n## Data Requirements\n\n" + table(["Data", "Source"], ["Payment record", "Bank"])
        self.assertEqual(list(by_type(extract(md), "data_requirement")), ["Payment record"])

    # The same table under a heading that names no type stays text.
    def test_table_under_untyped_heading_stays_text(self):
        md = "# Doc\n\n## Overview\n\n" + table(["Item", "Amount"], ["Licence", "180,000"])
        self.assertEqual(extract(md).tables[0].mapped_to, "text only")

    # An ordered list under "Current Process" gives steps, and a step that names a system uses it.
    def test_current_process_gives_steps_that_use_systems(self):
        md = (
            "# Doc\n\n## Interfaces\n\n"
            + table(["Connected system", "Direction"], ["PermitTrack", "Both ways"])
            + "\n## Current Process\n\n1. The applicant brings a form.\n2. Staff type it into PermitTrack.\n"
        )
        res = extract(md)
        steps = by_type(res, "process_step")
        self.assertEqual(len(steps), 2)
        system = by_type(res, "system")["PermitTrack"].slug
        self.assertEqual([p.links.get("uses_system") for p in steps.values()], [None, [system]])

    # An item that uses a glossary term links to it.
    def test_item_using_a_glossary_term_links_to_it(self):
        md = (
            "# Doc\n\n## Glossary\n\n" + table(["Term", "Meaning"], ["Plans examiner", "Checks drawings."])
            + "\n## Requirements\n\n- FR-1 The plans examiner shall mark up drawings.\n"
        )
        res = extract(md)
        (term,) = by_type(res, "glossary_term").values()
        (fr,) = by_type(res, "functional_requirement").values()
        self.assertEqual(fr.links["uses_term"], [term.slug])

    # A system is also found by its acronym, but not when two systems share it.
    def test_system_acronym_is_an_alias_only_when_unique(self):
        def uses(systems: list[str]) -> list:
            md = "# Doc\n\n## Interfaces\n\n" + table(["Connected system", "Direction"], *[[n, "In"] for n in systems])
            md += "\n## Requirements\n\n- FR-1 The system shall call the KYC service.\n"
            res = extract(md)
            return next(iter(by_type(res, "functional_requirement").values())).links.get("uses_system", [])

        self.assertEqual(len(uses(["identity/KYC verification service"])), 1)
        self.assertEqual(uses(["KYC service A", "KYC service B"]), [])

    # A requirement that implements a requirement serving an objective serves it too.
    def test_requirement_inherits_the_objective_of_what_it_implements(self):
        md = (
            "# Doc\n\n## Project Objectives\n\n1. Go online.\n\n## Business Requirements\n\n"
            + table(["ID", "Requirement", "Objective"], ["BR-01", "Apply online.", "1"])
            + "\n## Functional Requirements\n\n- FR-1 The system shall take applications. [BR-01]\n"
        )
        res = extract(md)
        (objective,) = by_type(res, "business_objective").values()
        (fr,) = by_type(res, "functional_requirement").values()
        self.assertEqual(fr.links["achieves"], [objective.slug])


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
