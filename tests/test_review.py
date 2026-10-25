# What this file does: tests the schema review. It covers recording gaps, the rule checks, the
# safeguards on decisions, the AI suggestions (with a fake client), and the full flow of applying
# and undoing decisions. The end-to-end tests are skipped unless gbrain is installed.

"""Schema gaps and their review: recording, rule checks, safeguards, applying and undoing."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from docingest.ingestion import review_ai
from docingest.ingestion.review_proposals import ReviewBoard, ReviewError
from docingest.ingestion.pipeline import Pipeline
from tests.test_list_extraction import VOCAB, extract
from tests.test_table_extraction import table

# True when gbrain is installed, so the end-to-end tests can run.
HAS_GBRAIN = bool(shutil.which("gbrain") or (Path.home() / ".bun/bin/gbrain").exists())


# A small BRD: two requirements, and a Data Migration table whose prefix (DM) is not in the
# vocabulary. The caller names the table's last column.
def harbor(rows: int = 3, column: str = "Related") -> str:
    """A BRD with requirements and a Data Migration table (prefix DM- is not in the vocabulary)."""
    dm = [[f"DM-0{i}", f"Migrate set {i}", "Legacy CRM", f"FR-00{(i % 2) + 1}"] for i in range(1, rows + 1)]
    return (
        "# Harbor BRD\n\n## 3. Functional Requirements\n\n"
        + table(["ID", "Requirement"], ["FR-001", "Export orders"], ["FR-002", "Import catalog"])
        + "\n## 7. Data Migration\n\n"
        + table(["ID", "Item", "Source", column], *dm)
    )


# A small document with scope items and deliverables. The caller names the deliverables' last
# column, which links to a scope item.
def scope_doc(header: str) -> str:
    """Deliverables whose column holds scope item IDs: two verbs fit (within_scope, delivers)."""
    return (
        "# D\n\n## 2. In Scope\n\n"
        + table(["ID", "Scope item"], ["SC-1", "Reports"], ["SC-2", "Exports"])
        + "\n## 4. Deliverables\n\n"
        + table(["ID", "Deliverable", header], ["DEL-1", "Report pack", "SC-1"], ["DEL-2", "Export pack", "SC-2"])
    )


# Gaps are recorded for unknown prefixes and unmapped columns, and not for known or generic ones.
class GapRecordingTest(unittest.TestCase):
    # A row with the unknown prefix DM becomes one gap that lists all its rows.
    def test_unknown_prefix_is_a_gap_with_every_row(self):
        gaps = extract(harbor(rows=3)).gaps
        self.assertEqual([(g["kind"], g["prefix"], len(g["rows"])) for g in gaps], [("unknown_prefix", "DM", 3)])
        self.assertEqual(gaps[0]["rows"][0]["cells"]["Item"], "Migrate set 1")

    # A 'Related' column is generic, so it makes no gap.
    def test_generic_related_column_is_no_gap(self):
        self.assertEqual([g for g in extract(harbor()).gaps if g["kind"] != "unknown_prefix"], [])

    # A column that maps to a known verb makes no gap.
    def test_known_prefixes_make_no_gap(self):
        self.assertEqual(extract(scope_doc("Delivers")).gaps, [])  # the column maps to a verb

    # A column with no known verb is a gap, listing the rows it covers.
    def test_unmapped_column_is_a_gap(self):
        gaps = extract(scope_doc("Scope Link")).gaps
        self.assertEqual([(g["kind"], g["column"], len(g["rows"])) for g in gaps], [("unmapped_column", "Scope Link", 2)])


# Gaps become proposals. The rule checks decide whether a proposal applies on its own.
class ProposalTest(unittest.TestCase):
    # Create a temporary work folder and a review board for each test.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = ReviewBoard(Path(self.tmp.name))

    # Remove the temporary folder after each test.
    def tearDown(self):
        self.tmp.cleanup()

    # Record the gaps of a document, and return the proposal they made.
    def propose(self, md: str, mode: str = "auto") -> dict:
        res = extract(md)
        ids = self.board.add_gaps("snippet", res.gaps, VOCAB, mode)
        return self.board.pending()[next(iter(ids.values()))]

    # Three rows pass every rule check, so the proposal applies on its own.
    def test_three_rows_pass_the_rule_checks(self):
        p = self.propose(harbor(rows=3))
        self.assertTrue(p["auto"])
        self.assertEqual(p["rules_suggestion"]["name"], "data_migration")

    # Two rows are too few for the rules, so a reviewer must decide.
    def test_two_rows_wait_for_a_reviewer(self):
        p = self.propose(harbor(rows=2))
        self.assertFalse(p["auto"])
        self.assertIn("only 2 row", p["why_not_automatic"])

    # In review mode nothing applies on its own, however clear the gap is.
    def test_review_mode_never_auto_applies(self):
        self.assertFalse(self.propose(harbor(rows=3), "review")["auto"])

    # The same prefix from two documents gives one proposal holding the rows of both.
    def test_same_prefix_from_two_documents_is_one_proposal(self):
        res = extract(harbor(rows=2))
        self.board.add_gaps("a", res.gaps, VOCAB, "auto")
        self.board.add_gaps("b", extract(harbor(rows=2)).gaps, VOCAB, "auto")
        (p,) = self.board.pending().values()
        self.assertEqual((p["documents"], len(p["rows"])), (["a", "b"], 4))
        self.assertTrue(p["checks"][0]["ok"] is False or len(p["rows"]) >= 3)

    # A column whose header does not name one verb gets alternatives and a hint, but does not apply
    # by itself.
    def test_column_with_overlapping_words_is_suggested_but_not_automatic(self):
        p = self.propose(scope_doc("Scope Link"))
        self.assertFalse(p["auto"])  # the header does not spell out one verb
        self.assertEqual({a["name"] for a in p["alternatives"]}, {"within_scope", "delivers"})
        self.assertEqual(p["suggestion"]["target"], "within_scope")  # a hint from the wording only

    # A column whose header spells out one verb is applied by the rules.
    def test_column_that_spells_out_one_verb_is_applied_by_rules(self):
        p = self.propose(scope_doc("Within Scope"))
        self.assertTrue(p["auto"])
        self.assertEqual((p["rules_suggestion"]["action"], p["rules_suggestion"]["target"]), ("alias", "within_scope"))

    # A gap the vocabulary already rejects is not proposed again, and is marked unmapped.
    def test_rejected_gap_is_not_proposed_again(self):
        vocab = self.board.vocab({"unmapped": {"DM": "rejected"}})
        res = extract(harbor())
        self.assertEqual(self.board.add_gaps("snippet", res.gaps, vocab, "auto"), {})
        self.assertEqual(res.gaps[0]["status"], "unmapped")


# A decision is refused when it is invalid. A decision needs enough reviewers, and the right note.
class SafeguardTest(unittest.TestCase):
    # Create a review board with two proposals (a new type and a column verb) for each test.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = ReviewBoard(Path(self.tmp.name))
        res = extract(harbor(rows=2))
        self.pid = next(iter(self.board.add_gaps("snippet", res.gaps, VOCAB, "auto").values()))
        res = extract(scope_doc("Tied To"))
        self.cid = next(iter(self.board.add_gaps("snippet", res.gaps, VOCAB, "auto").values()))

    # Remove the temporary folder after each test.
    def tearDown(self):
        self.tmp.cleanup()

    # Return the refusal message for a decision that must be refused.
    def refused(self, pid: str, text: str) -> str:
        p = self.board.pending()[pid]
        with self.assertRaises(ReviewError) as ctx:
            self.board.build(p, self.board.parse(p, text), VOCAB, {})
        return str(ctx.exception)

    # Mapping a column to a verb that does not allow its types is refused.
    def test_map_to_a_verb_that_does_not_fit_is_refused(self):
        self.assertIn("does not allow", self.refused(self.cid, "map:owned_by"))

    # A prefix cannot map to a structural type, or to a shared type (which is named, not numbered).
    def test_map_to_a_structural_or_shared_type_is_refused(self):
        self.assertIn("not a type", self.refused(self.pid, "map:record"))
        self.assertIn("shared type", self.refused(self.pid, "map:person"))

    # A new type cannot take a name already in use, or a name that is not valid.
    def test_new_type_names_may_not_clash(self):
        self.assertIn("already a type or verb", self.refused(self.pid, "rename:risk"))
        self.assertIn("already a type or verb", self.refused(self.pid, "rename:achieves"))
        self.assertIn("not a valid name", self.refused(self.pid, "rename:Data Migration"))

    # A decision the parser does not know ('maybe') is refused.
    def test_unknown_decisions_are_refused(self):
        with self.assertRaises(ReviewError):
            self.board.parse(self.board.pending()[self.pid], "maybe")

    # A new type needs a note and two different reviewers. The same person twice does not count.
    def test_a_new_type_needs_two_reviewers_and_a_note(self):
        p = self.board.pending()[self.pid]
        action = self.board.parse(p, "approve")
        need, note = self.board.required(p, action, {"pages": 1})
        self.assertEqual((need, note), (2, "a new type"))
        p = self.board.record(self.pid, "approve", "ana", None)
        self.assertIn("note", self.board.ready(p, VOCAB)[1])
        p = self.board.record(self.pid, "approve", "ana", "migration tasks")
        self.assertIn("needs 2 different reviewers", self.board.ready(p, VOCAB)[1])
        p = self.board.record(self.pid, "approve", "ana", "again")  # the same person twice
        self.assertIn("has 1", self.board.ready(p, VOCAB)[1])
        p = self.board.record(self.pid, "approve", "raj", "agreed")
        self.assertEqual(self.board.ready(p, VOCAB)[0]["name"], "data_migration")

    # A reviewer who changes the decision starts the approvals again.
    def test_a_different_decision_resets_the_approvals(self):
        self.board.record(self.pid, "approve", "ana", "x")
        p = self.board.record(self.pid, "rename:migration_item", "raj", "x")
        self.assertEqual([a["by"] for a in p["approvals"]], ["raj"])

    # Mapping as the rules suggest needs one reviewer, because it does not go against a suggestion.
    def test_one_reviewer_is_enough_to_map_as_suggested(self):
        p = self.board.pending()[self.cid]
        self.assertEqual(p["suggestion"]["action"], "none")
        p = self.board.record(self.cid, "map:within_scope", "ana", "reads as scope")
        need, note = self.board.required(p, self.board.parse(p, "map:within_scope"), {"pages": 2})
        self.assertEqual(need, 1)  # no suggestion to go against, an alias to an existing verb
        self.assertEqual(self.board.ready(p, VOCAB)[0]["target"], "within_scope")

    # A decision typed into a proposal file is recorded as an approval.
    def test_yaml_edit_is_picked_up(self):
        p = self.board.pending()[self.pid]
        p.update(decision="reject", reviewer="ana", note="not needed")
        self.board.save(p)
        self.board.sync_files()
        self.assertEqual(self.board.pending()[self.pid]["approvals"][0]["by"], "ana")

    # An invalid decision typed into a file is refused, and the refusal is logged.
    def test_yaml_edit_with_an_invalid_decision_is_refused_and_logged(self):
        p = self.board.pending()[self.pid]
        p.update(decision="map:owned_by", reviewer="ana")
        self.board.save(p)
        self.board.sync_files()
        self.assertEqual(self.board.pending()[self.pid]["approvals"], [])
        self.assertIn("refused", [e["event"] for e in self.board.history()])


# Stands in for the AI client. It returns one fixed answer, so the tests never call the API.
class FakeClient:
    """Stands in for the chat client: returns one canned structured answer."""

    # Keep the answer to return.
    def __init__(self, answer: dict):
        self.answer = answer

    # Return the canned answer as JSON text, the way the proxy's reply would carry it.
    def complete(self, system, user, schema, name="answer"):
        return json.dumps(self.answer), {"input": 0, "output": 0, "cache_read": 0}


# The AI's suggestions are kept only when they quote the rows and pass the checks. They are never
# applied by themselves.
class AISuggestionTest(unittest.TestCase):
    # Create a review board with a column proposal for each test.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = ReviewBoard(Path(self.tmp.name))
        res = extract(scope_doc("Tied To"))
        self.cid = next(iter(self.board.add_gaps("snippet", res.gaps, VOCAB, "auto").values()))

    # Remove the temporary folder after each test.
    def tearDown(self):
        self.tmp.cleanup()

    # Ask the AI for a suggestion about the column proposal. The given answer fields override the
    # defaults.
    def ask(self, **answer) -> dict | None:
        base = {"action": "map_to_existing", "target": "within_scope", "name": "", "reason": "scope", "evidence": "SC-1"}
        return review_ai.suggest(FakeClient({**base, **answer}), VOCAB, self.board.pending()[self.cid])

    # A suggestion whose quote appears in the rows is kept, and marked as the AI's.
    def test_a_quoted_suggestion_is_kept(self):
        s = self.ask()
        self.assertEqual((s["by"], s["action"], s["target"]), ("ai", "alias", "within_scope"))

    # A suggestion whose quote is not in the rows is dropped.
    def test_a_quote_not_in_the_rows_is_dropped(self):
        self.assertIsNone(self.ask(evidence="this text is not there"))

    # An answer of 'leave generic' gives no suggestion.
    def test_leave_generic_gives_no_suggestion(self):
        self.assertIsNone(self.ask(action="leave_generic"))

    # A suggestion that fails the decision checks is not stored on the proposal.
    def test_a_suggestion_that_breaks_the_checks_is_not_stored(self):
        client = FakeClient({"action": "map_to_existing", "target": "owned_by", "name": "", "reason": "x", "evidence": "SC-1"})
        added = review_ai.suggest_all(self.board, self.board.pending(), VOCAB, client=client)
        self.assertEqual(added, 0)
        self.assertNotIn("ai_suggestion", self.board.pending()[self.cid])

    # A valid suggestion is stored on the proposal, but the decision stays pending.
    def test_a_valid_suggestion_fills_the_proposal_but_does_not_apply(self):
        client = FakeClient({"action": "map_to_existing", "target": "delivers", "name": "", "reason": "x", "evidence": "SC-1"})
        self.assertEqual(review_ai.suggest_all(self.board, self.board.pending(), VOCAB, client=client), 1)
        p = self.board.pending()[self.cid]
        self.assertEqual((p["suggestion"]["by"], p["suggestion"]["target"], p["decision"]), ("ai", "delivers", "pending"))


# Full runs in a throwaway brain: decisions are applied, undone, rejected or rolled back. Skipped
# without gbrain.
@unittest.skipUnless(HAS_GBRAIN, "gbrain not installed")
class EndToEndTest(unittest.TestCase):
    """Ingest into a throwaway brain; apply, undo and roll back decisions."""

    # Create a throwaway work folder, brain and review board for each test.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.doc, self.work = root / "harbor.md", root / "work"
        self.pipe = Pipeline(self.work, root / "brain")
        self.board = ReviewBoard(self.work)

    # Remove the temporary folder after each test.
    def tearDown(self):
        self.tmp.cleanup()

    # The types of this document's DM rows, as stored in its manifest.
    def types(self) -> set[str]:
        m = json.loads(next((self.work / "manifests").glob("harbor*.json")).read_text())
        return {p["type"] for p in m["pages"] if p["fields"].get("item_id", "").startswith("DM-")}

    # A clear gap is applied by the rules during the same ingest, and its rows get the new type.
    def test_clear_gap_is_applied_by_the_rules_in_the_same_run(self):
        self.doc.write_text(harbor(rows=3))
        result = self.pipe.ingest([self.doc])
        self.assertEqual(self.types(), {"data_migration"})
        self.assertEqual((result["proposals_applied"], result["proposals_pending"]), (["P-0001"], []))
        self.assertIn("applied", [e["event"] for e in self.board.history()])
        self.assertTrue((self.work / "vocabulary.learned.yaml").exists())

    # An unclear gap waits for two reviewers. Once applied, undoing it puts the rows back as they
    # were.
    def test_unclear_gap_waits_then_two_reviewers_apply_it(self):
        self.doc.write_text(harbor(rows=2))
        result = self.pipe.ingest([self.doc])
        self.assertEqual((self.types(), result["proposals_pending"]), ({"record"}, ["P-0001"]))
        page = next(p for p in json.loads((self.work / "manifests" / "harbor.json").read_text())["pages"] if p["fields"].get("item_id") == "DM-01")
        self.assertEqual((page["fields"]["schema_status"], page["fields"]["proposal"]), ("provisional", "P-0001"))

        self.board.record("P-0001", "approve", "ana", "migration tasks")
        self.pipe.ingest([])
        self.assertEqual(self.types(), {"record"})  # one reviewer is not enough
        self.board.record("P-0001", "approve", "raj", "agreed")
        result = self.pipe.ingest([])
        self.assertEqual((self.types(), result["proposals_applied"]), ({"data_migration"}, ["P-0001"]))

        self.board.revert("P-0001", "ana")
        self.pipe._reload_vocabulary()
        self.pipe._reingest(["harbor"], {"processed": [], "failed": [], "stale": [], "timings": {}, "parents": {}})
        self.assertEqual(self.types(), {"record"})

    # A rejected gap keeps its rows generic, and no new proposal is opened on the next run.
    def test_rejected_gap_stays_generic_and_is_not_proposed_again(self):
        self.doc.write_text(harbor(rows=2))
        self.pipe.ingest([self.doc])
        self.board.record("P-0001", "reject", "ana", "not a real type")
        result = self.pipe.ingest([self.doc])
        self.assertEqual((self.types(), result["proposals_pending"]), ({"record"}, []))
        m = json.loads((self.work / "manifests" / "harbor.json").read_text())
        self.assertEqual(m["gaps"][0]["status"], "unmapped")

    # If verification fails after a decision is applied, the decision is rolled back and the
    # proposal reopens.
    def test_failed_verification_rolls_the_decision_back(self):
        self.doc.write_text(harbor(rows=3))
        self.pipe.schema_mode = "review"
        self.pipe.ingest([self.doc])
        self.board.record("P-0001", "approve", "ana", "x")
        self.board.record("P-0001", "approve", "raj", "x")
        original = self.pipe.verify
        self.pipe.verify = lambda keys, n=0: {**original(keys, n), "summary": {"status": "fail"}}
        result = self.pipe.ingest([])
        self.assertEqual((result["proposals_rolled_back"], result["proposals_pending"]), (["P-0001"], ["P-0001"]))
        self.assertEqual(self.types(), {"record"})
        self.assertEqual(self.board.learned().get("applied"), {})


if __name__ == "__main__":
    unittest.main()
