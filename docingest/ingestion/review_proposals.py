# What this file does: the review of schema gaps. When extraction cannot place something (an
# unknown ID prefix, or a column with no verb), it records a gap. This file turns gaps into
# proposals and decides who settles each one: the rules (when every check passes), an AI suggestion
# (never applied by itself), or a person. Files live in <work>/review/.

"""Review of schema gaps: what the vocabulary did not cover, and who decides.

Extraction records a gap wherever it fell back (an unknown ID prefix typed `record`, a
column whose cells became generic `references` links). This module turns gaps into
proposals, one per prefix or column across all documents, and decides who settles each:

  1. rules   every check passes: the proposal is applied and logged
  2. AI      (review_ai.py) writes a suggestion into the proposal; it never applies itself
  3. person  approves, maps to an existing type or verb, or rejects

Files, in <work>/review/: pending/P-0007.yaml, decided/P-0007.yaml, PENDING.md,
decisions.log (JSON lines). Approved additions go to <work>/vocabulary.learned.yaml.
A document is ingested with its safe fallback until a proposal is applied.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import yaml

from docingest.utils import clock
from docingest.utils.data_types import Converted
from docingest.vocabulary.vocabulary_loader import LEARNED_NAME, Vocabulary, norm_header

MIN_PREFIX_ROWS = 3  # rows that must share an unknown prefix before rules make it a type
BIG_CHANGE_PAGES = 20  # a decision changing more pages than this needs two reviewers
# How many days of applied decisions the page and the verification report list.
RECENT_DAYS = 7
# Page types that are structure rather than vocabulary. A proposal never maps to them.
STRUCTURAL = {"source_document", "document_section", "record"}
# A valid new type name: lower-case letters, digits and underscores, 3 to 41 characters.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
# Common words that are dropped when a type name is made from a heading or a column header.
STOP_WORDS = {"and", "of", "the", "a", "an", "for", "to", "in", "by", "id", "name", "item"}
# The note at the top of each proposal file. It tells the reviewer what to write to decide.
GUIDANCE = (
    "# Reviewer: set `decision:` to approve | map:<existing type or verb> | rename:<new name> |\n"
    "# reject, put your name in `reviewer:`, and save (or use `./ingest.sh review approve|map|reject`).\n"
    "# New types, rejections and decisions against the suggestion need a one-line `note:`.\n"
    "# New types and large changes need two different reviewers.\n"
    "# When unsure, reject: the data stays generic but correct, and you can approve later.\n"
)


# A YAML writer with no anchors or aliases, so the proposal files read as plain text.
class _PlainDumper(yaml.SafeDumper):
    """YAML without anchors and aliases, so reviewers read plain text."""

    # Tell the YAML writer never to use anchors (&id) or aliases (*id).
    def ignore_aliases(self, data):
        return True


# Write a dict as YAML, with the plain writer and the keys in their original order.
def _dump(data: dict) -> str:
    return yaml.dump(data, Dumper=_PlainDumper, sort_keys=False, allow_unicode=True, width=100)


# Raised when a decision or command is refused. The message says why, and the reviewer sees it.
class ReviewError(Exception):
    """A decision or command the review system refuses."""


# The review folder, <work>/review.
def review_dir(work: Path) -> Path:
    """Folder reviewers work in."""
    return work / "review"


# The file of approved vocabulary additions, inside the work folder.
def learned_file(work: Path) -> Path:
    """Approved vocabulary additions."""
    return work / LEARNED_NAME


# The current time, for file names and the audit log.
def now() -> str:
    """Timestamp for files and logs."""
    return clock.iso()


# The name of the person deciding. Order: the --by flag, then DOCINGEST_USER, then the OS user.
def reviewer_name(by: str | None = None) -> str:
    """Who is deciding: --by, then DOCINGEST_USER, then the OS user."""
    return by or os.environ.get("DOCINGEST_USER") or getpass.getuser()


# Build the learned vocabulary from every decision still in force. Each decision's additions are
# merged in, so reverting one removes only its own additions.
def compose(applied: dict) -> dict:
    """The learned vocabulary from the per-proposal additions that are in force."""
    out: dict = {"version": 0, "types": {}, "prefixes": {}, "verbs": {}, "columns": {},
                 "unmapped": {}, "applied": applied}  # fmt: skip
    for delta in applied.values():
        for k in ("types", "prefixes", "verbs", "columns", "unmapped"):
            out[k].update(delta.get(k) or {})
    return out


# Suggest a type name from the heading the rows sit under. Stop words are dropped, the last word is
# made singular, and at most three words are kept.
def type_name_for(heading: str, prefix: str) -> str:
    """A type name from the section heading: "7. Data Migrations" -> data_migration."""
    words = [w for w in norm_header(re.sub(r"^[\d.\s]+", "", heading)).split() if w not in STOP_WORDS]
    if not words:
        return f"{prefix.lower()}_record"
    last = words[-1]
    # Make the last word singular: 'categories' becomes 'category', and 'data migrations' becomes
    # 'data migration'.
    if last.endswith("ies"):
        words[-1] = last[:-3] + "y"
    elif last.endswith("s") and not last.endswith(("ss", "us", "is")):
        words[-1] = last[:-1]
    return "_".join(words[-3:])[:40]


# The meaningful words of a text: lower case, without stop words or very short words. Used to
# compare names and headings.
def _words(text: str) -> set[str]:
    return {w for w in norm_header(text.replace("_", " ")).split() if w not in STOP_WORDS and len(w) > 2}


# Compare two actions by their kind and their target. Used to tell whether a reviewer went against
# a suggestion.
def decision_sig(action: dict) -> tuple:
    """What a decision or suggestion amounts to, for comparing them."""
    return (action["action"], action.get("name") or action.get("target"))


# The review folder of one work directory. It holds the proposals and the audit trail, and it is
# the one place where decisions are recorded, checked and applied.
class ReviewBoard:
    """The review folder of one work directory."""

    # Remember the folders. Open proposals and settled proposals are kept in separate folders.
    def __init__(self, work: Path):
        self.work = work
        self.dir = review_dir(work)
        self.pending_dir = self.dir / "pending"
        self.decided_dir = self.dir / "decided"

    # ---- storage ----------------------------------------------------------------

    # Read one proposal file. An empty file gives an empty dict.
    def _read(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text()) or {}

    # Write one proposal file. The guidance note goes first, so a reviewer who opens the file sees
    # how to decide.
    def _write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(GUIDANCE + _dump(data))

    # Read every open proposal file.
    def pending(self) -> dict[str, dict]:
        """Open proposals by ID."""
        return {p.stem: self._read(p) for p in sorted(self.pending_dir.glob("P-*.yaml"))}

    # Read every settled proposal file.
    def decided(self) -> dict[str, dict]:
        """Settled proposals by ID."""
        return {p.stem: self._read(p) for p in sorted(self.decided_dir.glob("P-*.yaml"))}

    # Find a proposal, open or settled, by its ID.
    def get(self, pid: str) -> dict:
        """A proposal (open or settled), or ReviewError."""
        for folder in (self.pending_dir, self.decided_dir):
            if (folder / f"{pid}.yaml").exists():
                return self._read(folder / f"{pid}.yaml")
        raise ReviewError(f"No proposal {pid}. List them with: review list")

    # Save an open proposal in the pending folder.
    def save(self, p: dict) -> None:
        """Write an open proposal."""
        self._write(self.pending_dir / f"{p['id']}.yaml", p)

    # The next free proposal ID (P-0001, P-0002, ...). Open and settled proposals both count.
    def next_id(self) -> str:
        """The next unused proposal ID."""
        ids = [int(f.stem[2:]) for d in (self.pending_dir, self.decided_dir) for f in d.glob("P-*.yaml")]
        return f"P-{max(ids, default=0) + 1:04d}"

    # Read the learned vocabulary file. An empty dict when there is none.
    def learned(self) -> dict:
        """The learned vocabulary file, or an empty one."""
        path = learned_file(self.work)
        return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}

    # The vocabulary with the learned additions, or with the ones given. A decision is tested this
    # way before it is saved.
    def vocab(self, learned: dict | None = None) -> Vocabulary:
        """The vocabulary with the learned additions (or the given ones)."""
        return Vocabulary(learned=self.learned() if learned is None else learned)

    # Save the learned vocabulary from the decisions in force. Its version goes up by one, so
    # gbrain notices the change.
    def write_learned(self, applied: dict) -> None:
        """Save the learned vocabulary built from the additions in force."""
        data = compose(applied)
        data["version"] = int(self.learned().get("version", 0)) + 1
        learned_file(self.work).write_text(
            "# Reviewed additions to vocabulary.yaml. Written by docingest; use `review revert`.\n"
            + _dump(data)
        )

    # Append one event to the audit trail (decisions.log). Lines are only ever added, never edited.
    def log(self, event: str, **kv) -> None:
        """Append to the audit trail."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "decisions.log").open("a") as f:
            f.write(json.dumps({"at": now(), "event": event, **kv}, ensure_ascii=False) + "\n")

    # Read the whole audit trail, oldest first.
    def history(self) -> list[dict]:
        """Every audit entry, oldest first."""
        path = self.dir / "decisions.log"
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

    # Applied, reverted and rolled-back decisions from the last few days. The page and the
    # verification report list them.
    def recent(self, days: int = RECENT_DAYS) -> list[dict]:
        """Applied or reverted decisions in the last ``days`` days."""
        cutoff = time.time() - days * 86400
        out = []
        for e in self.history():
            try:
                t = time.mktime(time.strptime(e["at"][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            if t >= cutoff and e["event"] in ("applied", "reverted", "rolled_back"):
                out.append(e)
        return out

    # Hold a lock file while a run changes proposals, so `review apply` and `ingest` cannot both do
    # it at once. A lock left behind by a process that is no longer running is ignored.
    @contextmanager
    def lock(self):
        """Keep `review apply` and `ingest` from running at the same time."""
        path = self.dir / ".lock"
        self.dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                # Signal 0 only checks whether the process in the lock file is still alive. Nothing
                # is sent to it.
                os.kill(int(path.read_text().strip()), 0)
                raise ReviewError(f"Another docingest run holds {path} (pid {path.read_text().strip()})")
            except (ProcessLookupError, ValueError):
                pass  # a dead run left it behind
        path.write_text(str(os.getpid()))
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    # Whether this person may approve a new type. When reviewers.yaml lists the approvers, only
    # they may.
    def approvers_allowed(self, name: str) -> bool:
        """True unless review/reviewers.yaml lists who may approve new types and omits ``name``."""
        path = self.dir / "reviewers.yaml"
        if not path.exists():
            return True
        return name in (self._read(path).get("new_type_approvers") or [name])

    # ---- gaps to proposals ------------------------------------------------------

    def add_gaps(self, doc_key: str, gaps: list[dict], vocab: Vocabulary, mode: str) -> dict[str, str]:
        """Record a document's gaps; returns gap key -> proposal ID.

        One proposal per prefix or column across documents: rows from earlier runs of the
        same document are replaced. Rejected gaps and mismatches get no proposal.
        """
        ids: dict[str, str] = {}
        if mode == "off":
            return ids
        for gap in gaps:
            if gap["kind"] == "verb_mismatch":
                continue
            ukey = gap["key"].split(":", 1)[1]
            # A gap that a reviewer already rejected stays generic, and gets no new proposal.
            if ukey in vocab.unmapped or gap["key"] in vocab.unmapped:
                gap["status"] = "unmapped"
                continue
            # Reuse the open proposal for this gap if there is one, so every document adds its rows
            # to the same proposal.
            existing = next((p for p in self.pending().values() if p["key"] == gap["key"]), None)
            # A new proposal starts from what the gap says. Its kind follows the kind of gap.
            p = existing or {
                "id": self.next_id(),
                "key": gap["key"],
                "kind": "new_type" if gap["kind"] == "unknown_prefix" else "column_verb",
                "created": now(),
                "found": "",
                "heading": gap["heading"],
                "header": gap.get("header", []),
                "column": gap.get("column"),
                "prefix": gap.get("prefix"),
                "title_column": gap.get("title_column"),
                "documents": [],
            }
            # Drop this document's rows from an earlier run, so a re-run does not count them twice.
            p["rows"] = [r for r in p.get("rows", []) if r.get("doc") != doc_key]
            p["rows"] += [{**r, "doc": doc_key} for r in gap["rows"]]
            # The documents that have rows in this proposal.
            p["documents"] = sorted(set(p["documents"]) | {doc_key})
            # Run the rule checks again, now that the rows have changed.
            self.evaluate(p, vocab, mode)
            self.save(p)
            ids[gap["key"]] = p["id"]
            # The gap points at its proposal, so the page and the report can show it.
            gap["proposal"] = p["id"]
        return ids

    # ---- tier 1: rules ------------------------------------------------------------

    # Tier 1, the rules. Run the checks for this kind of proposal, then decide whether it may apply
    # on its own.
    def evaluate(self, p: dict, vocab: Vocabulary, mode: str = "auto") -> None:
        """Run the rule checks, rank alternatives, and set the rules' suggestion and `auto`."""
        if p["kind"] == "new_type":
            checks, suggestion, alternatives = self._eval_prefix(p, vocab)
        else:
            checks, suggestion, alternatives = self._eval_column(p, vocab)
        p["checks"], p["rules_suggestion"], p["alternatives"] = checks, suggestion, alternatives
        # A proposal applies on its own only in auto mode, with a suggestion, and with every check
        # passing.
        p["auto"] = bool(mode == "auto" and suggestion["action"] != "none" and all(c["ok"] for c in checks))
        p["why_not_automatic"] = (
            "" if p["auto"] else (
                "schema mode is review: nothing is applied without a reviewer" if mode == "review"
                else "; ".join(c["detail"] for c in checks if not c["ok"]) or "no rule suggestion"
            )
        )
        self._choose(p)

    # Choose which suggestion to show, and write the one-line summary of what was found.
    @staticmethod
    def _choose(p: dict) -> None:
        """The suggestion shown: the rules' when they have one, else the AI's."""
        rules, ai = p["rules_suggestion"], p.get("ai_suggestion")
        # Prefer the rules' suggestion. The AI's is shown only when the rules have none.
        p["suggestion"] = rules if rules["action"] != "none" or not ai else ai
        n = len(p["rows"])
        what = f"prefix {p['prefix']}-" if p["kind"] == "new_type" else f"column \"{p['column']}\""
        p["found"] = f"{what} ({n} row{'s' if n != 1 else ''}) in \"{p['heading']}\""
        p.setdefault("decision", "pending")
        p.setdefault("reviewer", None)
        p.setdefault("note", None)
        p.setdefault("approvals", [])

    # Checks and suggestion for an unknown ID prefix: a free type name, enough rows, and the
    # closest existing types as alternatives.
    def _eval_prefix(self, p: dict, vocab: Vocabulary):
        # The suggested type name, taken from the heading.
        name = type_name_for(p["heading"], p["prefix"])
        errors = self.name_errors(name, vocab, p["prefix"])
        # The checks. A single row is not a pattern, so enough rows must share the prefix. The name
        # must be free.
        checks = [
            {"name": f"at least {MIN_PREFIX_ROWS} rows share the prefix", "ok": len(p["rows"]) >= MIN_PREFIX_ROWS,
             "detail": f"only {len(p['rows'])} row(s) (needs {MIN_PREFIX_ROWS})"},
            {"name": "name is free", "ok": not errors, "detail": "; ".join(errors) or "free"},
        ]
        # The words that describe the rows: from the heading, the column headers and the prefix.
        q = _words(p["heading"]) | _words(" ".join(p["header"])) | _words(p["prefix"])
        ranked = []
        # Rank the existing document types by how many words they share with the rows.
        for t in vocab.types.values():
            if t.name in STRUCTURAL or t.scope != "document":
                continue
            have = _words(t.name) | {w for k in t.keywords + t.title_columns for w in _words(k)} | _words(t.description)
            if q & have:
                ranked.append((len(q & have), t))
        # The three closest existing types, most similar first, shown to the reviewer as
        # alternatives.
        alternatives = [
            {"name": t.name, "description": t.description, "score": s}
            for s, t in sorted(ranked, key=lambda x: (-x[0], x[1].name))[:3]
        ]
        # The rules' suggestion: a new type with the name, its folder and the prefix. 'none' when
        # the name is taken.
        suggestion = {
            "by": "rules", "action": "new_type" if not errors else "none", "name": name,
            "dir": f"docs/learned/{name.replace('_', '-')}", "prefixes": [p["prefix"]],
            "reason": f"Rows with IDs {p['prefix']}-… sit under \"{p['heading']}\"",
        }  # fmt: skip
        return checks, suggestion, alternatives

    # The (row type, target type) pairs of a column proposal, without duplicates. A verb must fit
    # all of them.
    def _pairs(self, p: dict) -> list[tuple[str, str]]:
        return sorted({(r["type"], t[1]) for r in p["rows"] for t in r["targets"] if t[1]})

    # Checks and suggestion for a column with no verb. The values must resolve to one pair of
    # types, one verb must fit every pair, and the header must name that verb.
    def _eval_column(self, p: dict, vocab: Vocabulary):
        pairs = self._pairs(p)
        # The verbs allowed for each pair of types.
        per_pair = [set(vocab.verbs_between(s, d)) for s, d in pairs]
        # The verbs allowed for every pair. A verb must fit all the rows.
        common = sorted(set.intersection(*per_pair)) if per_pair else []
        # The words of the column header.
        head = _words(p["column"])
        # The verbs whose words all appear in the column header. 'Depends on' names depends_on.
        named = [v for v in common if _words(v) and _words(v) <= head]
        checks = [
            # Every row and every target must be of a single type, so one verb can describe them
            # all.
            {"name": "values resolve to one type", "ok": len({d for _, d in pairs}) == 1 and len({s for s, _ in pairs}) == 1,
             "detail": "rows or targets of more than one type: " + ", ".join(f"{s}->{d}" for s, d in pairs)},
            # The header must not already be a known alias of another verb.
            {"name": "header is not another verb's alias", "ok": norm_header(p["column"]) not in vocab.column_map,
             "detail": f"\"{p['column']}\" already maps to {vocab.column_map.get(norm_header(p['column']))}"},
            # At least one verb must fit every row.
            {"name": "one verb fits every row", "ok": len(common) >= 1, "detail": "no verb fits all rows"},
            # The header must spell out exactly one of the fitting verbs. Otherwise the choice
            # would be a guess.
            {"name": "header names that verb", "ok": len(named) == 1,
             "detail": "the header does not spell out exactly one of: " + (", ".join(common) or "none")},
        ]
        # The closest existing verbs, ranked by how many words they share with the header and
        # description.
        alternatives = [
            {"name": v, "description": vocab.verbs[v].description,
             "from": vocab.verbs[v].from_, "to": vocab.verbs[v].to}
            for v in sorted(common, key=lambda v: (-len(head & _words(v + " " + vocab.verbs[v].description)), v))[:3]
        ]  # fmt: skip
        # Choose the verb: the one the header names, or the best alternative when its words match
        # the header.
        pick = named[0] if len(named) == 1 else (alternatives[0]["name"] if alternatives and head & _words(
            alternatives[0]["name"] + " " + alternatives[0]["description"]) else None)
        # The suggestion: alias the column to that verb, or make no suggestion when there is no
        # clear fit.
        suggestion = (
            {"by": "rules", "action": "alias", "target": pick,
             "reason": f"\"{p['column']}\" reads as {pick}: {vocab.verbs[pick].description}"}
            if pick else
            {"by": "rules", "action": "none", "reason": "No existing verb is a clear fit; links stay generic"}
        )  # fmt: skip
        return checks, suggestion, alternatives

    # Check a new type name against the vocabulary: it has a valid form, is not already a type or
    # verb, is not a keyword of an existing type, and its ID prefix is not held by another type.
    def name_errors(self, name: str, vocab: Vocabulary, prefix: str | None = None) -> list[str]:
        """Why a new type name cannot be used (empty when it is free)."""
        errs = []
        if not NAME_RE.match(name):
            errs.append(f"\"{name}\" is not a valid name (lower case letters, digits and underscores)")
        # Names already in use: types, verbs and their reverse names.
        taken = set(vocab.types) | set(vocab.verbs) | {v.inverse for v in vocab.verbs.values() if v.inverse}
        taken |= {t.gbrain_type for t in vocab.types.values()}
        if name in taken:
            errs.append(f"\"{name}\" is already a type or verb")
        if any(name.replace("_", " ") in t.keywords for t in vocab.types.values()):
            errs.append(f"\"{name}\" is a keyword of an existing type")
        if prefix and prefix.upper() in vocab.prefix_map:
            errs.append(f"prefix {prefix} already belongs to {vocab.prefix_map[prefix.upper()]}")
        return errs

    # ---- decisions ------------------------------------------------------------

    # Turn a decision string (approve, reject, map:..., rename:...) into an action. Anything that
    # does not fit this proposal is refused.
    def parse(self, p: dict, text: str) -> dict:
        """A decision string as an action; ReviewError when it makes no sense for this proposal."""
        text = (text or "").strip()
        if text == "reject":
            return {"action": "reject"}
        # Approve means the suggestion as it stands. With no suggestion, the reviewer must map or
        # reject.
        if text == "approve":
            s = p["suggestion"]
            if s["action"] == "none":
                raise ReviewError("There is no suggestion to approve: use map:<name> or reject")
            return dict(s)
        kind, _, arg = text.partition(":")
        arg = arg.strip()
        # Rename gives a new type its own name. Only a new-type proposal can be renamed.
        if kind == "rename" and p["kind"] == "new_type" and arg:
            return {"action": "new_type", "name": arg, "prefixes": [p["prefix"]],
                    "dir": f"docs/learned/{arg.replace('_', '-')}", "reason": "renamed by reviewer"}  # fmt: skip
        # Map sends the rows to a type (for an ID prefix) or a verb (for a column) that already
        # exists.
        if kind == "map" and arg:
            return {"action": "map_type" if p["kind"] == "new_type" else "alias", "target": arg}
        raise ReviewError(f"Unknown decision \"{text}\": use approve, map:<name>, rename:<name> or reject")

    # Build the vocabulary additions that an action makes, and check them. The additions are first
    # tested in a trial vocabulary, so an action that would break the vocabulary is refused before
    # it is saved.
    def build(self, p: dict, action: dict, vocab: Vocabulary, learned: dict) -> dict:
        """The vocabulary additions a decision makes; ReviewError when it would be invalid."""
        a = action["action"]
        # Rejecting adds the gap to the unmapped list, so it stays generic and is never proposed
        # again.
        if a == "reject":
            return {"unmapped": {p["key"].split(":", 1)[1]: f"rejected in {p['id']}"}}
        errors, delta = [], {}
        # A new type needs a valid and free name, a free folder, and the ID prefix it will hold.
        if a == "new_type":
            errors = self.name_errors(action["name"], vocab, p["prefix"])
            if any(t.dir == action["dir"] for t in vocab.types.values()):
                errors.append(f"folder {action['dir']} is taken")
            # The type to add: a document type with the prefix, and the title column its rows use.
            delta = {"types": {action["name"]: {
                "dir": action["dir"], "primitive": "annotation", "scope": "document", "group": "learned",
                "id_prefixes": [p["prefix"].upper()],
                "title_columns": [norm_header(p["title_column"])] if p.get("title_column") else [],
                "description": f"Reviewed type for {p['prefix']}- rows under \"{p['heading']}\" ({p['id']})",
            }}}  # fmt: skip
        # Map an ID prefix to an existing type. A shared type is named rather than numbered, so it
        # is refused.
        elif a == "map_type":
            t = vocab.types.get(action["target"])
            if not t or t.name in STRUCTURAL:
                errors.append(f"\"{action['target']}\" is not a type you can map to")
            elif t.scope != "document":
                errors.append(f"\"{t.name}\" is a shared type, named rather than numbered")
            # An ID prefix can belong to only one type.
            if p["prefix"].upper() in vocab.prefix_map:
                errors.append(f"prefix {p['prefix']} already belongs to {vocab.prefix_map[p['prefix'].upper()]}")
            delta = {"prefixes": {p["prefix"].upper(): {"type": action["target"]}}}
        # Alias a column to an existing verb. The verb must allow every pair of types the rows
        # link.
        elif a == "alias":
            verb = vocab.verbs.get(action["target"])
            if not verb or verb.name in vocab.UNTYPED_VERBS:
                errors.append(f"\"{action['target']}\" is not a verb a column can map to")
            else:
                bad = [f"{s}->{d}" for s, d in self._pairs(p) if not vocab.fits(verb.name, s, d)]
                if bad:
                    errors.append(f"{verb.name} does not allow {', '.join(bad)}")
            # A column that already maps to a verb cannot be mapped again.
            if norm_header(p["column"]) in vocab.column_map:
                errors.append(f"\"{p['column']}\" already maps to {vocab.column_map[norm_header(p['column'])]}")
            delta = {"columns": {norm_header(p["column"]): {"verb": action["target"]}}}
        else:
            errors.append(f"cannot apply an action of kind {a}")
        # The last check: load the vocabulary with this addition and compare its problems with the
        # current ones. Only new problems refuse the action.
        if not errors:
            before = set(vocab.problems())
            after = set(Vocabulary(learned=compose({**learned.get("applied", {}), p["id"]: delta})).problems())
            errors += sorted(after - before)
        if errors:
            raise ReviewError("; ".join(errors))
        return delta

    # Record one reviewer's decision after checking it. Nothing is applied here. That happens at
    # the next ingest, or with `review apply`.
    def record(self, pid: str, text: str, by: str, note: str | None = None) -> dict:
        """Record one reviewer's decision after checking it; returns the proposal."""
        p = self.pending().get(pid)
        if p is None:
            raise ReviewError(f"{pid} is not open")
        action = self.parse(p, text)
        self.build(p, action, self.vocab(), self.learned())  # refuse an invalid decision now
        self.preview(p, action)  # what it would change; also decides how many reviewers it needs
        # A new type needs an approver that the team allows in reviewers.yaml.
        if action["action"] == "new_type" and not self.approvers_allowed(by):
            raise ReviewError(f"{by} is not listed in review/reviewers.yaml as a new-type approver")
        # Approvals for a different decision are dropped, since the reviewer has changed their
        # mind.
        kept = [a for a in p["approvals"] if a["decision"] == text]
        # Approvals that disagree with the new decision are logged as a conflict.
        if len(kept) != len(p["approvals"]):
            self.log("conflict", proposal=pid, by=by, decision=text)
        # Each reviewer has one approval: their latest decision replaces their earlier one.
        kept = [a for a in kept if a["by"] != by] + [{"by": by, "at": now(), "decision": text, "note": note}]
        p.update(approvals=kept, decision=text, reviewer=by, note=note or p.get("note"))
        self.save(p)
        self.log("decision", proposal=pid, by=by, decision=text, note=note)
        return p

    # Pick up decisions that a reviewer typed into a proposal file, and record them the same way a
    # command would.
    def sync_files(self) -> None:
        """Turn `decision:` + `reviewer:` edited into a file into a recorded approval."""
        for pid, p in self.pending().items():
            text, who = (p.get("decision") or "pending"), p.get("reviewer")
            if text == "pending" or not who:
                continue
            # This approval is already recorded, so there is nothing to do.
            if any(a["by"] == who and a["decision"] == text for a in p["approvals"]):
                continue
            try:
                self.record(pid, text, who, p.get("note"))
            # A refused decision from a file is logged and kept on the proposal, so the reviewer
            # can see why.
            except ReviewError as e:
                self.log("refused", proposal=pid, by=who, decision=text, reason=str(e))
                p["refused"] = str(e)
                self.save(p)

    # How many different reviewers must approve a decision, and whether it needs a note. A decision
    # is risky when it creates a type, changes many pages, or goes against every suggestion.
    def required(self, p: dict, action: dict, impact: dict) -> tuple[int, str | None]:
        """(approvers needed, why a note is needed or None) for a decision."""
        # The suggestions that the decision can be measured against.
        sugg = [s for s in (p["rules_suggestion"], p.get("ai_suggestion")) if s and s["action"] != "none"]
        # A decision that goes against every suggestion needs a note.
        against = bool(sugg) and all(decision_sig(s) != decision_sig(action) for s in sugg)
        # Risky decisions need two different reviewers.
        risky = action["action"] == "new_type" or impact.get("pages", 0) > BIG_CHANGE_PAGES or against
        note = (
            "a new type" if action["action"] == "new_type" else "a rejection" if action["action"] == "reject"
            else "a decision against the suggestion" if against else None
        )
        # A rejection needs one reviewer, whatever its size.
        return (2 if risky and action["action"] != "reject" else 1), note

    # Can a recorded decision be applied now? It must have enough different reviewers, any required
    # note, and it must still build. Returns (action, "") when it can, otherwise (None, why not).
    def ready(self, p: dict, vocab: Vocabulary) -> tuple[dict | None, str]:
        """(action, "") when a proposal has enough approvals to apply, else (None, why not)."""
        # No decision has been recorded yet.
        if p["decision"] == "pending" or not p["approvals"]:
            return None, "no decision yet"
        text = p["decision"]
        # The reviewers who chose this decision. Approvals for other decisions do not count.
        who = {a["by"] for a in p["approvals"] if a["decision"] == text}
        try:
            action = self.parse(p, text)
            self.build(p, action, vocab, self.learned())
        except ReviewError as e:
            return None, str(e)
        impact = p.get("impact") or {}
        need, note_for = self.required(p, action, impact)
        # A decision that needs a note must have one.
        if note_for and not any(a.get("note") for a in p["approvals"] if a["decision"] == text):
            return None, f"{note_for} needs a note explaining why"
        # Not enough different reviewers yet.
        if len(who) < need:
            return None, f"needs {need} different reviewers, has {len(who)} ({', '.join(sorted(who))})"
        return action, ""

    # ---- commit / revert ------------------------------------------------------

    # Put a decision in force. It is saved to the learned vocabulary, and the proposal moves to the
    # decided folder. The caller then reads the affected documents again.
    def commit(self, p: dict, action: dict, by: str, how: str) -> dict:
        """Put a decision in force; returns the additions. The caller re-extracts the documents."""
        delta = self.build(p, action, self.vocab(), self.learned())
        # Start from the decisions already in force, then add this one.
        applied = dict(self.learned().get("applied") or {})
        applied[p["id"]] = delta
        # Record how the decision was applied, for the audit trail. A rejection is stored as
        # 'rejected'.
        p.update(status="rejected" if action["action"] == "reject" else "applied", applied_at=now(),
                 applied_by=by, applied_how=how, action=action, delta=delta)  # fmt: skip
        self.write_learned(applied)
        self._move(p, self.pending_dir, self.decided_dir)
        self.log("applied", proposal=p["id"], by=by, how=how, action=action, key=p["key"])
        return delta

    # Undo a decision that was applied, and reopen its proposal, with the reason. Used when
    # verification fails after a decision is applied.
    def rollback(self, pid: str, reason: str) -> None:
        """Undo an applied decision and reopen the proposal."""
        p = self.decided()[pid]
        applied = dict(self.learned().get("applied") or {})
        applied.pop(pid, None)
        self.write_learned(applied)
        # Remove the fields the decision added, so the proposal looks open again.
        for k in ("status", "applied_at", "applied_by", "applied_how", "action", "delta"):
            p.pop(k, None)
        # Clear the decision and the approvals. The reason stays on the proposal for the reviewer.
        p.update(decision="pending", reviewer=None, approvals=[], refused=reason)
        self._move(p, self.decided_dir, self.pending_dir)
        self.log("rolled_back", proposal=pid, reason=reason)

    # Remove a settled decision from the learned vocabulary. The proposal stays in the decided
    # folder, marked reverted.
    def revert(self, pid: str, by: str) -> dict:
        """Remove an applied decision from the learned vocabulary; the proposal stays settled."""
        p = self.decided().get(pid)
        # Only an applied or rejected decision can be reverted.
        if not p or p.get("status") not in ("applied", "rejected"):
            raise ReviewError(f"{pid} is not an applied decision")
        applied = dict(self.learned().get("applied") or {})
        applied.pop(pid, None)
        self.write_learned(applied)
        p.update(status="reverted", reverted_at=now(), reverted_by=by)
        self._write(self.decided_dir / f"{pid}.yaml", p)
        self.log("reverted", proposal=pid, by=by)
        return p

    # Write the proposal into its new folder, then remove it from the old one.
    def _move(self, p: dict, src: Path, dst: Path) -> None:
        self._write(dst / f"{p['id']}.yaml", p)
        (src / f"{p['id']}.yaml").unlink(missing_ok=True)

    # ---- impact preview -------------------------------------------------------

    # Measure what a decision would change, before it is made. Each document is read again in
    # memory with the decision's vocabulary, and the result is compared with the pages saved now.
    # Nothing is written.
    def preview(self, p: dict, action: dict) -> dict:
        """What a decision would change: re-extract the proposal's documents in memory.

        Returns counts of pages whose type changes and links whose verb changes, plus
        example sentences. Stored in the proposal so the approval rules can use it.
        """
        from docingest.ingestion.extract_with_rules import Extractor

        # Rejecting changes no page, so the preview is empty.
        if action["action"] == "reject":
            p["impact"] = {"documents": 0, "pages": 0, "types": 0, "links": 0, "examples": []}
            return p["impact"]
        delta = self.build(p, action, self.vocab(), self.learned())
        learned = self.learned()
        # The vocabulary as it would be after the decision.
        new_vocab = Vocabulary(learned=compose({**(learned.get("applied") or {}), p["id"]: delta}))
        types = links = pages = docs = 0
        examples: list[str] = []
        for key in p["documents"]:
            mpath = self.work / "manifests" / f"{key}.json"
            if not mpath.exists():
                continue
            m = json.loads(mpath.read_text())
            md = (self.work / "markdown" / f"{key}.md").read_text()
            ref = self.work / "reference" / f"{key}.txt"
            conv = Converted(markdown=md, converter=m["converter"])
            # Read the document again with the new vocabulary. Nothing is written.
            res = Extractor(new_vocab).extract(
                Path(m["source_path"]), conv, ref.read_text() if ref.exists() else "", m["kind"], key, m["sha256"]
            )
            docs += 1
            # The pages as they are saved now, keyed by item ID.
            old = {pg["fields"]["item_id"]: pg for pg in m["pages"] if pg["fields"].get("item_id")}
            slug_id = {pg.slug: pg.fields["item_id"] for pg in res.pages if pg.fields.get("item_id")}
            for pg in res.pages:
                item = pg.fields.get("item_id")
                before = old.get(item)
                if not before:
                    continue
                # Count the links by verb, so that a change of verb can be spotted.
                verbs = lambda links_: Counter(v for v, ts in links_.items() for _ in ts)  # noqa: E731
                # Links the item had before and no longer has after the decision.
                gone = verbs(before["links"]) - verbs(pg.links)
                # Links the item gains. A link that changed its verb shows in both gone and added.
                added = verbs(pg.links) - verbs(before["links"])
                # The item's type changes under the decision.
                if before["type"] != pg.type:
                    types += 1
                    if len(examples) < 6:
                        examples.append(f"{item}: type {before['type']} -> {pg.type}")
                # A changed link is counted, and an example of it is shown.
                if gone:
                    links += sum(gone.values())
                    for verb, targets in pg.links.items():
                        if verb in added and len(examples) < 6:
                            examples.append(f"{item} {verb} {', '.join(slug_id.get(t, t) for t in targets)}")
                # A page is counted once, whether its type or its links change.
                if before["type"] != pg.type or gone:
                    pages += 1
        # Store the impact on the proposal, so that the approval rules can use it.
        p["impact"] = {"documents": docs, "pages": pages, "types": types, "links": links, "examples": examples}
        return p["impact"]

    # ---- reports --------------------------------------------------------------

    # Write one proposal as markdown for `review show`: what was found, the checks, the
    # suggestions, the closest alternatives, every affected row, and the impact.
    def render(self, p: dict) -> str:
        """One proposal as markdown, for `review show`."""
        s, ai = p["suggestion"], p.get("ai_suggestion")
        lines = [f"## {p['id']}: {p['found']}", "", f"Documents: {', '.join(p['documents'])}", ""]
        lines += ["Checks:"] + [f"  {'✓' if c['ok'] else '✗'} {c['name']}" + ("" if c["ok"] else f" — {c['detail']}") for c in p["checks"]]
        lines += ["", f"Suggestion ({s['by']}): {self._describe(s)}", f"  Why: {s.get('reason', '')}"]
        # Show the AI's suggestion as well, with its quote, when it differs from the one shown.
        if ai and ai is not s:
            lines.append(f"AI suggestion: {self._describe(ai)} — {ai.get('reason', '')}")
            lines.append(f"  Quote: \"{ai.get('evidence', '')}\"")
        # The closest existing types or verbs, for the reviewer to consider.
        if p["alternatives"]:
            lines += ["", "Closest existing alternatives:"]
            for a in p["alternatives"]:
                lines.append(f"  - {a['name']}: {a['description']}" + (f" (from {a['from']} to {a['to']})" if "from" in a else ""))
        lines += ["", f"All {len(p['rows'])} affected row(s):"]
        # Every affected row, with the document it came from.
        for r in p["rows"]:
            if p["kind"] == "new_type":
                lines.append(f"  - [{r['doc']}] " + " | ".join(f"{k}: {v}" for k, v in r["cells"].items()))
            else:
                lines.append(f"  - [{r['doc']}] {r['id']} ({r['type']}): \"{r['cell']}\" -> "
                             + ", ".join(f"{t} ({d})" for t, d in r["targets"]))  # fmt: skip
        # The impact, when a preview has been made.
        if p.get("impact"):
            i = p["impact"]
            lines += ["", f"If approved: {i['pages']} page(s) change in {i['documents']} document(s) "
                      f"({i['types']} type change(s), {i['links']} link(s) change verb)."]  # fmt: skip
            lines += [f"  e.g. {x}" for x in i["examples"]]
        # The reason the last attempt was refused.
        if p.get("refused"):
            lines += ["", f"Last attempt refused: {p['refused']}"]
        if p["approvals"]:
            lines += ["", "Approvals: " + "; ".join(f"{a['by']} ({a['decision']})" for a in p["approvals"])]
        lines += ["", "When unsure, reject: the data stays generic but correct, and you can approve later."]
        return "\n".join(lines) + "\n"

    # A one-line description of a suggestion, in words a reviewer understands.
    @staticmethod
    def _describe(s: dict) -> str:
        return {"new_type": f"new type {s.get('name')} for prefix {', '.join(s.get('prefixes', []))}",
                "map_type": f"map to existing type {s.get('target')}",
                "alias": f"read the column as verb {s.get('target')}",
                "none": "no suggestion (leave generic)"}.get(s["action"], s["action"])  # fmt: skip

    # Rewrite PENDING.md, the one page reviewers read: a table of open proposals, and the decisions
    # of the last week.
    def write_pending_md(self) -> Path:
        """Rewrite PENDING.md: the one page reviewers read."""
        # The newest proposals come first.
        rows = sorted(self.pending().values(), key=lambda p: p["id"], reverse=True)
        out = ["# Open schema proposals", "", f"Updated {now()}. Details: `./ingest.sh review show <ID>`.", ""]
        if rows:
            out += ["| ID | Kind | What was found | Suggested | By | Documents | Approvals |", "|---|---|---|---|---|---|---|"]
            for p in rows:
                s = p["suggestion"]
                out.append(f"| {p['id']} | {p['kind'].replace('_', ' ')} | {p['found']} | {self._describe(s)} | {s['by']} | "
                           f"{', '.join(p['documents'])} | {len(p['approvals'])} |")  # fmt: skip
        else:
            out.append("Nothing to review.")
        recent = self.recent()
        if recent:
            out += ["", f"## Applied in the last {RECENT_DAYS} days", ""]
            out += [f"- {e['at'][:10]} {e['event']} {e['proposal']} by {e.get('by', '?')}" for e in recent]
        path = self.dir / "PENDING.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(out) + "\n")
        return path
