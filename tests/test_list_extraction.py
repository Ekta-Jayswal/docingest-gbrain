# What this file does: tests that list items become entities. An item is typed by its own ID, or by
# a heading that names exactly one type. It also checks the routing that decides whether the AI
# must read a list.

"""List items become entities: typed by their own ID, else by a heading that names one type."""

from __future__ import annotations

import unittest
from pathlib import Path

from docingest.ingestion.extract_with_rules import Extractor
from docingest.utils.data_types import Converted
from docingest.utils.markdown_parsing import parse_lists, split_sections
from docingest.vocabulary.vocabulary_loader import Vocabulary

# One vocabulary, shared by all the tests in this file.
VOCAB = Vocabulary()


# Run the rule extraction on a short piece of markdown and return the result.
def extract(md: str):
    """Run rule extraction on a markdown snippet."""
    conv = Converted(markdown=md, converter="markdown")
    return Extractor(VOCAB).extract(Path("snippet.md"), conv, md, "brd", "snippet", "0")


# Map each item ID (or shared name) to its (gbrain type, title), for easy comparison.
def entities(res) -> dict[str, tuple[str, str]]:
    """Item ID -> (gbrain type, title) of the entity pages and shared entities."""
    out = {p.fields["item_id"]: (p.type, p.title) for p in res.pages if "item_id" in p.fields}
    for g in res.globals:
        out[g.name] = (VOCAB.gbrain_type(g.vocab_type), g.name)
    return out


# Map each name to the page, or shared entity, of one vocabulary type.
def by_type(res, vocab_type: str) -> dict:
    """Name -> page or shared entity of one type."""
    gtype = VOCAB.gbrain_type(vocab_type)
    out = {p.title.split(": ", 1)[-1]: p for p in res.pages if p.type == gtype}
    out |= {g.name: g for g in res.globals if g.vocab_type == vocab_type}
    return out


# The entity page that carries this item ID.
def page(res, item_id: str):
    """The entity page of an item ID."""
    return next(p for p in res.pages if p.fields.get("item_id") == item_id)


# The list finder: its heading, lead-in line, items and ordering.
class ParseListsTest(unittest.TestCase):
    """Lists are found with their heading and lead-in; sub-items and tables are not items."""

    # A list gets its heading and lead-in line. Continued lines join their item, and a sub-item is
    # not an item.
    def test_items_heading_and_lead_in(self):
        md = (
            "# Doc\n\n## Scope\n\nThe work does not include:\n\n"
            "- First thing\n\n- Second thing\n  continued here\n"
            "    - a sub-item\n\nAfter the list.\n"
        )
        (block,) = parse_lists(md, split_sections(md))
        self.assertEqual(block.heading, "Scope")
        self.assertEqual(block.lead_in, "The work does not include:")
        self.assertEqual(block.items, ["First thing", "Second thing continued here"])
        self.assertFalse(block.ordered)

    # A numbered list is marked ordered, and its items are read in order.
    def test_numbered_list_is_ordered(self):
        md = "## Steps\n\n1. One\n2. Two\n"
        (block,) = parse_lists(md, split_sections(md))
        self.assertTrue(block.ordered)
        self.assertEqual(block.items, ["One", "Two"])


# Which list items become which entities.
class ListEntitiesTest(unittest.TestCase):
    """Which list items become which entities."""

    # Items under a heading that names one type all become entities of that type.
    def test_heading_names_the_type(self):
        res = extract("# Doc\n\n## Assumptions\n\n- Staff are trained.\n- Data is clean.\n")
        types = {t for t, _ in entities(res).values()}
        self.assertEqual(len(entities(res)), 2)
        self.assertEqual(types, {VOCAB.gbrain_type("assumption")})

    # 'Special Requirements' items are constraints. 'Requirements' items are requirements.
    def test_special_requirements_are_constraints_not_requirements(self):
        res = extract("# Doc\n\n## Special Requirements\n\n- Work on site only.\n- No weekend access.\n")
        types = {t for t, _ in entities(res).values()}
        self.assertEqual(len(entities(res)), 2)
        self.assertEqual(types, {VOCAB.gbrain_type("constraint")})
        res = extract("# Doc\n\n## Requirements\n\n- The tool exports orders.\n- The tool imports orders.\n")
        self.assertEqual({t for t, _ in entities(res).values()}, {VOCAB.gbrain_type("requirement")})

    # A heading that names two types is ambiguous, so its items stay as text.
    def test_heading_naming_two_types_stays_text(self):
        res = extract("# Doc\n\n## Assumptions and Dependencies\n\n- A thing.\n- B thing.\n")
        self.assertEqual(entities(res), {})

    # A heading that names no type leaves its items as text.
    def test_heading_naming_no_type_stays_text(self):
        res = extract("# Doc\n\n## Background\n\n- A thing.\n- B thing.\n")
        self.assertEqual(entities(res), {})

    # 'Non-goals' names no type, because 'non' negates the keyword 'goals'.
    def test_negated_keyword_names_no_type(self):
        self.assertIsNone(VOCAB.single_type_for_heading("Non-goals"))
        self.assertEqual(
            VOCAB.single_type_for_heading("Non-Functional Requirements"),
            "nonfunctional_requirement",
        )

    # Items under 'Included:' are in scope. Items under 'Not included:' are out of scope.
    def test_scope_in_and_out_from_lead_in(self):
        res = extract(
            "# Doc\n\n## Scope\n\nIncluded:\n\n- Web forms\n\n"
            "Not included:\n\n- Mobile app\n"
        )
        scopes = {p.title.split(": ", 1)[1]: p.fields.get("scope") for p in res.pages
                  if p.fields.get("item_id")}  # fmt: skip
        self.assertEqual(scopes, {"Web forms": "in", "Mobile app": "out"})

    # An item with its own ID takes its type from that ID. A trace such as [BR-01] becomes a link.
    def test_item_with_its_own_id_and_a_trace(self):
        md = (
            "# Doc\n\n## Business Requirements\n\n| ID | Requirement |\n|---|---|\n"
            "| BR-01 | Apply online. |\n\n## Applicant\n\n"
            "- FR-1.1 The system shall accept uploads. [BR-01]\n"
            "- **FR-1.2** The system shall show status. (BR-01)\n"
        )
        res = extract(md)
        fr = page(res, "FR-1.1")
        self.assertEqual(fr.type, VOCAB.gbrain_type("functional_requirement"))
        self.assertEqual(fr.title, "FR-1.1: The system shall accept uploads.")
        br = page(res, "BR-01").slug
        self.assertEqual(fr.links.get("implements"), [br])
        self.assertEqual(page(res, "FR-1.2").links.get("implements"), [br])

    # Numbered items are process steps, each following the one before. Bullets under the same
    # heading are not steps.
    def test_bullets_are_never_steps_numbered_items_follow(self):
        res = extract("# Doc\n\n## Process Steps\n\n1. Apply.\n2. Review.\n3. Decide.\n")
        steps = [p for p in res.pages if p.type == VOCAB.gbrain_type("process_step")]
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[2].links.get("follows"), [steps[1].slug])
        res = extract("# Doc\n\n## Process Steps\n\n- Apply.\n- Review.\n")
        self.assertEqual(entities(res), {})

    # An 'Integrations: a, b' item makes one system per name. A system is linked only when its name
    # matches in the right case.
    def test_label_lists_systems_and_text_names_them(self):
        md = (
            "# Doc\n\n## In Scope\n\n- Integrations: core banking system, Notify.\n\n"
            "## Requirements\n\n| ID | Requirement |\n|---|---|\n"
            "| REQ-1 | Post the loan to the Core Banking System. |\n"
            "| REQ-2 | We notify the applicant by email. |\n"
        )
        res = extract(md)
        systems = {g.name for g in res.globals if g.vocab_type == "system"}
        self.assertEqual(systems, {"core banking system", "Notify"})
        core = next(g.slug for g in res.globals if g.name == "core banking system")
        self.assertEqual(page(res, "REQ-1").links.get("uses_system"), [core])
        # A one-word name must match its case: "notify" is a verb here, not the system.
        self.assertNotIn("uses_system", page(res, "REQ-2").links)

    # Names in bullet points never become people.
    def test_people_never_come_from_bullets(self):
        res = extract("# Doc\n\n## Stakeholders\n\n- Jane Doe\n- Team: Ann Lee, Bo Chan\n")
        self.assertEqual(entities(res), {})


# Headings that are items: a heading with an ID, or a named item under a persona, epic or feature
# heading.
class HeadingItemsTest(unittest.TestCase):
    """Headings that are items: own ID, or a named item under a persona/epic/feature heading."""

    # A small document with two personas, two user stories and their acceptance criteria.
    STORIES = (
        "# Doc\n\n## Personas\n\n### Shopper\n\nBuys things.\n\n### Clerk\n\nPacks them.\n\n"
        "## User stories\n\n### US-1: Pay online\n\nAs a shopper, I want to pay by card, "
        "so that I skip the till.\n\nAcceptance criteria:\n\n"
        "- Given a full basket, when I pay, then I get a receipt.\n"
        "- The page loads in two seconds.\n\n### US-2: Pack orders\n\n"
        "As the clerk, I want a pick list.\n\n- Given a paid order, when it is packed, "
        "then it leaves the queue.\n"
    )

    # An ID heading becomes a user story, and sub-headings under 'Personas' become personas.
    def test_id_headings_and_named_sub_headings(self):
        res = extract(self.STORIES)
        self.assertEqual(set(by_type(res, "user_story")), {"Pay online", "Pack orders"})
        self.assertEqual(set(by_type(res, "persona")), {"Shopper", "Clerk"})
        self.assertEqual(page(res, "US-1").title, "US-1: Pay online")

    # Each story is linked to the persona named in its 'As a ...' line.
    def test_story_links_to_its_persona(self):
        res = extract(self.STORIES)
        personas = by_type(res, "persona")
        self.assertEqual(page(res, "US-1").links.get("performed_by"), [personas["Shopper"].slug])
        self.assertEqual(page(res, "US-2").links.get("performed_by"), [personas["Clerk"].slug])

    # Acceptance criteria are linked to the story they sit under.
    def test_criteria_verify_the_story_they_sit_under(self):
        res = extract(self.STORIES)
        criteria = by_type(res, "acceptance_criterion")
        self.assertEqual(len(criteria), 3)  # a lead-in "Acceptance criteria:" types plain bullets
        owners = {c.title.split(": ", 1)[-1]: c.links.get("verifies") for c in criteria.values()}
        us1, us2 = page(res, "US-1").slug, page(res, "US-2").slug
        self.assertEqual(owners["Given a full basket, when I pay, then I get a receipt."], [us1])
        self.assertEqual(owners["The page loads in two seconds."], [us1])
        self.assertEqual(owners["Given a paid order, when it is packed, then it leaves the queue."], [us2])

    # Sub-headings under a requirements heading are not items, because requirements are statements.
    def test_sub_headings_under_a_statement_type_are_not_items(self):
        md = (
            "# Doc\n\n## Functional Requirements\n\n### Applicant\n\ntext\n\n"
            "### Inspector\n\ntext\n"
        )
        self.assertEqual(entities(extract(md)), {})

    # A story is linked to a persona only when the names match.
    def test_persona_must_match_by_name(self):
        md = (
            "# Doc\n\n## Personas\n\n### Shopper\n\ntext\n\n## Stories\n\n"
            "### US-1: Pay\n\nAs a visitor, I want to pay.\n"
        )
        self.assertNotIn("performed_by", page(extract(md), "US-1").links)


# Whether a list counts as fully read by the rules. That decides whether it goes to the AI.
class RoutingTest(unittest.TestCase):
    """A list the rules fully extracted is not text the AI must read."""

    # The routing decision for every section of a short markdown snippet.
    def route(self, md: str) -> list[dict]:
        from docingest.ingestion.rule_confidence import route_sections

        res = extract(md)
        return route_sections(res, res.globals)

    # The route ('rules' or 'ai') of the section with this heading.
    def route_of(self, md: str, heading: str) -> str:
        return next(d["route"] for d in self.route(md) if d["heading"] == heading)

    # A list whose every item became an entity is read by the rules, so it is not sent to the AI.
    def test_fully_extracted_list_routes_to_rules(self):
        md = "# Doc\n\n## Applicant\n\n- FR-1.1 The system shall accept uploads.\n- FR-1.2 The system shall show status.\n"
        self.assertEqual(self.route_of(md, "Applicant"), "rules")

    # Plain prose items that the rules cannot type are sent to the AI.
    def test_unextracted_items_still_route_to_ai(self):
        md = "# Doc\n\n## Background\n\n- The team agreed to meet every week to review the plan.\n- Budget notes are kept by finance and shared on request.\n"
        self.assertEqual(self.route_of(md, "Background"), "ai")

    # A list with some items extracted and some not still goes to the AI.
    def test_partly_extracted_list_still_routes_to_ai(self):
        md = "# Doc\n\n## Applicant\n\n- FR-1.1 The system shall accept uploads.\n- The office is open on weekdays between nine and five every day.\n"
        self.assertEqual(self.route_of(md, "Applicant"), "ai")


if __name__ == "__main__":
    unittest.main()
