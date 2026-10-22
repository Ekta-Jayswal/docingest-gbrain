# What this file does: tests the self-test's baseline comparison. Small drops pass; real drops and
# wrong items fail.

"""The self-test's baseline comparison: small drops pass, real drops and wrong items fail."""

from __future__ import annotations

import unittest

from docingest.evaluation.selftest import compare

# A baseline score row for one answer key, to compare against.
BASE = {
    "doc": {
        "overall": 0.95,
        "links_f1": 0.9,
        "entities_precision": 1.0,
        "links_precision": 1.0,
    }
}


# A copy of the baseline row, with the given scores changed.
def row(**changes) -> dict:
    """The baseline row of 'doc' with some values changed."""
    return {"doc": {**BASE["doc"], **changes}}


# What counts as a regression, and what is only reported.
class CompareTest(unittest.TestCase):
    """What counts as a regression."""

    # Identical scores give no problems and no improvements.
    def test_same_scores_pass(self):
        self.assertEqual(compare(row(), BASE), ([], []))

    # A drop of less than one point is within tolerance.
    def test_a_drop_within_a_point_passes(self):
        problems, _ = compare(row(links_f1=0.895), BASE)
        self.assertEqual(problems, [])

    # A drop of more than a point is one problem, and the message names the stage that fell.
    def test_a_bigger_drop_fails_and_names_the_stage(self):
        problems, _ = compare(row(links_f1=0.8), BASE)
        self.assertEqual(len(problems), 1)
        self.assertIn("links_f1", problems[0])

    # Precision must not drop at all, even by a small amount.
    def test_any_precision_drop_fails(self):
        problems, _ = compare(row(entities_precision=0.98), BASE)
        self.assertIn("entities_precision", problems[0])

    # A rise is an improvement: it is reported, but never a failure.
    def test_a_rise_is_reported_not_failed(self):
        problems, improved = compare(row(overall=0.99), BASE)
        self.assertEqual(problems, [])
        self.assertIn("overall", improved[0])

    # An answer key with no baseline, and a baseline key that was not run, both count as problems.
    def test_unknown_and_missing_keys_fail(self):
        problems, _ = compare({"new": BASE["doc"]}, BASE)
        self.assertEqual(len(problems), 2)  # no baseline for 'new'; 'doc' was not run


if __name__ == "__main__":
    unittest.main()
