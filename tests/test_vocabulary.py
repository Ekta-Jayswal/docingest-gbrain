# What this file does: tests the vocabulary (vocabulary.yaml). It checks that the vocabulary is
# consistent, that the reference document is current, and that verbs and types resolve as they
# should.

"""Vocabulary consistency, and the generated reference staying in step with it."""

from __future__ import annotations

import unittest
from pathlib import Path

from docingest.vocabulary.vocabulary_loader import Vocabulary

# The project root, two levels above this file.
ROOT = Path(__file__).resolve().parent.parent


# Checks on the vocabulary file and on how link verbs are chosen.
class VocabularyTest(unittest.TestCase):
    """vocabulary.yaml is sound and docs/VOCABULARY.md matches it."""

    # Load the vocabulary before each test.
    def setUp(self):
        self.v = Vocabulary()

    # The vocabulary has no consistency problems.
    def test_no_problems(self):
        self.assertEqual(self.v.problems(), [])

    # docs/VOCABULARY.md matches what the vocabulary generates. It fails when the YAML was edited
    # without regenerating the reference.
    def test_reference_is_up_to_date(self):
        current = (ROOT / "docs" / "VOCABULARY.md").read_text()
        self.assertEqual(
            current,
            self.v.render_markdown(),
            "Regenerate: python -m docingest vocab > docs/VOCABULARY.md",
        )

    # Verbs renamed in earlier vocabulary versions still resolve to their current names.
    def test_old_names_map_to_new_verbs(self):
        self.assertEqual(self.v.canonical_verb("supports"), "achieves")
        self.assertEqual(self.v.canonical_verb("mentions"), "mentions_item")
        self.assertEqual(self.v.canonical_verb("limits"), "constrains")
        self.assertEqual(self.v.canonical_verb("achieves"), "achieves")

    # A verb with one target type records that type. A verb with no single target records none.
    def test_single_target_is_enforced(self):
        self.assertEqual(self.v.verbs["achieves"].target, "business_objective")
        self.assertIsNone(self.v.verbs["measures"].target)

    # The same column name gives different verbs, depending on the types on each side of the link.
    def test_verb_chosen_by_types(self):
        # The same column name means different verbs in different tables.
        self.assertEqual(self.v.choose_verb(None, "payment_term", "milestone"), "paid_on")
        self.assertEqual(self.v.choose_verb(None, "deliverable", "milestone"), "scheduled_for")
        self.assertEqual(
            self.v.choose_verb(None, "acceptance_criterion", "user_story"), "verifies"
        )
        self.assertEqual(self.v.choose_verb(None, "risk", "functional_requirement"), "threatens")
        self.assertEqual(
            self.v.choose_verb(None, "functional_requirement", "business_objective"), "achieves"
        )

    # A column's verb is used only when its types fit. Otherwise the link stays generic.
    def test_column_verb_only_when_types_fit(self):
        # "Delivers" on a payment row is a payment trigger, not a delivery.
        self.assertEqual(self.v.choose_verb("delivers", "payment_term", "deliverable"), "paid_on")
        # "Achieves" pointing at a KPI fits no verb, so the link stays generic.
        self.assertEqual(self.v.choose_verb("achieves", "epic", "kpi"), "references")
        self.assertEqual(
            self.v.choose_verb("implements", "functional_requirement", "business_requirement"),
            "implements",
        )

    # A type group expands to its members, and 'any' means no restriction.
    def test_type_groups_expand(self):
        self.assertIn("functional_requirement", self.v.expand(["requirement_types"]))
        self.assertEqual(self.v.expand(["any"]), [])


if __name__ == "__main__":
    unittest.main()
