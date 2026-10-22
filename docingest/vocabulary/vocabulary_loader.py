# What this file does: loads the vocabulary (page types, link verbs, document kinds) from
# vocabulary.yaml, merges in reviewed additions, answers questions such as "which verb fits
# this link", and writes the gbrain schema pack and the vocabulary reference document.

"""Vocabulary loading and gbrain schema-pack generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml

# The built-in vocabulary file, next to this code.
VOCAB_PATH = Path(__file__).with_name("vocabulary.yaml")
# Name of the file that holds reviewed additions. It lives in the work folder, not in the code.
LEARNED_NAME = "vocabulary.learned.yaml"  # reviewed additions, in the work folder


# Read the reviewed additions. Returns an empty dict when there are none.
def load_learned(learned: Path | dict | None) -> dict:
    """The learned vocabulary: a dict, a YAML file, or empty when there is none."""
    if isinstance(learned, dict):
        return learned
    if learned and Path(learned).exists():
        return yaml.safe_load(Path(learned).read_text()) or {}
    return {}


# Add reviewed types, ID prefixes, verbs and columns to the raw vocabulary, in place.
def merge_learned(raw: dict, learned: dict) -> None:
    """Add the learned types, ID prefixes, column names and verbs to the raw vocabulary.

    Learned entries only add: an entry that would replace a built-in one is ignored,
    and Vocabulary.problems() reports any clash it leaves.
    """
    for name, t in (learned.get("types") or {}).items():
        raw["types"].setdefault(name, dict(t))
    # A learned ID prefix is added only to a type that already exists.
    for prefix, e in (learned.get("prefixes") or {}).items():
        if e["type"] in raw["types"]:
            prefixes = raw["types"][e["type"]].setdefault("id_prefixes", [])
            if prefix not in prefixes:
                prefixes.append(prefix)
    for name, v in (learned.get("verbs") or {}).items():
        raw["verbs"].setdefault(name, dict(v))
    for header, e in (learned.get("columns") or {}).items():
        if e["verb"] in raw["verbs"]:
            columns = raw["verbs"][e["verb"]].setdefault("columns", [])
            if header not in columns:
                columns.append(header)
    # Any learned change bumps the pack's version, so gbrain picks up the new schema.
    if learned.get("version"):
        # gbrain wants semver: every change to the learned vocabulary raises the patch number.
        major, minor, patch = (int(x) for x in str(raw["pack"]["version"]).split("."))
        raw["pack"]["version"] = f"{major}.{minor}.{patch + int(learned['version'])}"


# Normalise a heading or column name so that small differences in case, punctuation or
# spacing still match.
def norm_header(text: str) -> str:
    """Normalize a column header or heading: lower case, letters and digits, single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


# Column name without a bracketed qualifier, so "Baseline (Today)" matches "Baseline".
def header_key(text: str) -> str:
    """A column header without its qualifier: "Baseline (Today)" -> "baseline"."""
    return norm_header(re.sub(r"\(.*?\)", " ", text)) or norm_header(text)


# One page type from the vocabulary: its folder, how it is stored in gbrain, and the
# keywords, ID prefixes and columns that identify it in documents.
@dataclass
class TypeDef:
    """One page type from the vocabulary."""

    name: str  # vocabulary name (e.g. organization)
    gbrain_type: str  # page type stored in gbrain (e.g. company)
    dir: str
    primitive: str
    scope: str
    group: str = ""
    builtin: bool = False
    extractable: bool = False
    id_prefixes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    title_columns: list[str] = field(default_factory=list)
    keyless_columns: list[str] = field(default_factory=list)
    heading_items: bool = False
    description: str = ""


# One link verb from the vocabulary: its reverse name, the types it may connect, the
# columns that mean it, and its description.
@dataclass
class VerbDef:
    """One relationship verb from the vocabulary."""

    name: str
    inverse: str | None
    target: str | None  # the one allowed target type, when `to` names a single type
    columns: list[str]
    temporal: str | None
    description: str
    group: str = ""
    from_: list[str] = field(default_factory=list)  # as written: types, type groups or `any`
    to: list[str] = field(default_factory=list)
    evidence: str = ""
    docs: list[str] = field(default_factory=list)
    status: str = ""
    replaces: list[str] = field(default_factory=list)


# The whole vocabulary, loaded from YAML and kept in memory. Every other part of the
# pipeline asks this class what a heading, column or link means.
class Vocabulary:
    """The vocabulary (page types, verbs, document fields and kinds) loaded from YAML."""

    # Load the YAML, merge in the reviewed additions, and build the type, verb and document-field
    # tables from them.
    def __init__(self, path: Path = VOCAB_PATH, learned: Path | dict | None = None):
        raw = yaml.safe_load(path.read_text())
        self.learned = load_learned(learned)
        merge_learned(raw, self.learned)
        # Gaps a reviewer rejected: kept generic and never proposed again.
        self.unmapped: dict[str, str] = dict(self.learned.get("unmapped") or {})
        self.pack = raw["pack"]
        self.types: dict[str, TypeDef] = {}
        for name, t in raw["types"].items():
            self.types[name] = TypeDef(
                name=name,
                gbrain_type=t.get("gbrain_type", name),
                dir=t["dir"].strip("/"),
                primitive=t["primitive"],
                scope=t["scope"],
                builtin=t.get("builtin", False),
                extractable=t.get("extractable", False),
                id_prefixes=t.get("id_prefixes", []),
                group=t.get("group", ""),
                keywords=[norm_header(k) for k in t.get("keywords", [])],
                title_columns=[norm_header(c) for c in t.get("title_columns", [])],
                keyless_columns=[norm_header(c) for c in t.get("keyless_columns", [])],
                heading_items=t.get("heading_items", False),
                description=t.get("description", ""),
            )
        self.type_groups: dict[str, list[str]] = raw.get("type_groups", {})
        self.verbs: dict[str, VerbDef] = {}
        for name, v in raw["verbs"].items():
            # A verb's targets come from `to`, or from the older single `target` field, or are
            # `any`.
            to = v.get("to", [v["target"]] if v.get("target") else ["any"])
            # Remember the target only when it names exactly one real type (used by person_verbs).
            single = to[0] if len(to) == 1 and to[0] in raw["types"] else None
            self.verbs[name] = VerbDef(
                name=name,
                inverse=v.get("inverse"),
                target=single,
                columns=[norm_header(c) for c in v.get("columns", [])],
                temporal=v.get("temporal"),
                description=v.get("description", ""),
                group=v.get("group", ""),
                from_=v.get("from", ["any"]),
                to=to,
                evidence=v.get("evidence", ""),
                docs=v.get("docs", []),
                status=v.get("status", ""),
                replaces=v.get("replaces", []),
            )
        self.document_fields = {norm_header(k): v for k, v in raw["document_fields"].items()}
        self.document_kinds = raw["document_kinds"]

    # Map each old verb name to the verb that replaced it, so older documents still resolve.
    @cached_property
    def verb_aliases(self) -> dict[str, str]:
        """Earlier verb names to the verb that now covers them (``achieves`` -> ``supports``)."""
        return {old: v.name for v in self.verbs.values() for old in v.replaces}

    # Return the current name of a verb, translating names from earlier vocabulary versions.
    def canonical_verb(self, name: str) -> str:
        """The current name for a verb, translating names from earlier vocabulary versions."""
        return self.verb_aliases.get(name, name)

    # Replace each type group with the types it contains. `any` returns an empty list, which
    # means no restriction.
    def expand(self, names: list[str]) -> list[str]:
        """Type names with type groups expanded; ``any`` becomes an empty list (no limit)."""
        if "any" in names:
            return []
        out: list[str] = []
        for n in names:
            out += self.type_groups.get(n, [n])
        return out

    # Verbs never chosen by type alone: structural ones, and those that fit any pair.
    UNTYPED_VERBS = frozenset(
        {"references", "depends_on", "defined_in", "section_of", "mentions", "appears_in"}
    )

    # True when the verb may run from a source type to a destination type. A verb with no
    # restriction on a side accepts any type on that side.
    def fits(self, verb: str, src: str, dst: str) -> bool:
        """True if the vocabulary allows ``verb`` from a ``src`` page to a ``dst`` page."""
        v = self.verbs[verb]
        sources, targets = self.expand(v.from_), self.expand(v.to)
        return (not sources or src in sources) and (not targets or dst in targets)

    # List the verbs that are allowed between two types. Only the most specific ones are returned.
    def verbs_between(self, src: str, dst: str) -> list[str]:
        """The verbs allowed from ``src`` to ``dst``, most specific first.

        A verb that names both types beats one that accepts any type on one side.
        """
        ranked = []
        for v in self.verbs.values():
            sources, targets = self.expand(v.from_), self.expand(v.to)
            # Skip verbs that are never chosen by type, and verbs that have no type restriction at
            # all.
            if v.name in self.UNTYPED_VERBS or (not sources and not targets):
                continue
            if self.fits(v.name, src, dst):
                # Rank 0 when both sides are restricted (most specific), 1 when only one side is.
                ranked.append((0 if sources and targets else 1, v.name))
        best = min((rank for rank, _ in ranked), default=None)
        return [name for rank, name in ranked if rank == best]

    # Pick the verb for one link: the column's verb if the types allow it, otherwise the single
    # verb that fits, otherwise the generic "references".
    def choose_verb(self, column_verb: str | None, src: str, dst: str) -> str:
        """The verb for a link from a ``src`` row to a ``dst`` item named in a cell.

        The column's verb wins when the types allow it; otherwise the one verb the
        vocabulary allows between the two types; otherwise ``references``. A verb is
        never written between types it does not allow ("specific but wrong").
        """
        # The column's own verb wins when the two types allow it.
        if column_verb and self.fits(column_verb, src, dst):
            return column_verb
        candidates = self.verbs_between(src, dst)
        return candidates[0] if len(candidates) == 1 else "references"

    # Check the vocabulary for mistakes (unknown types, duplicate columns or prefixes, missing
    # inverses). An empty list means it is sound.
    def problems(self) -> list[str]:
        """Consistency errors in the vocabulary (empty when it is sound)."""
        errs = []
        # Names a verb may legally use. Anything else is a typo in the YAML.
        known = set(self.types) | set(self.type_groups) | {"any"}
        for g, members in self.type_groups.items():
            errs += [f"type group {g}: unknown type {m}" for m in members if m not in self.types]
        seen_cols: dict[str, str] = {}
        names = set(self.verbs) | {v.inverse for v in self.verbs.values() if v.inverse}
        for v in self.verbs.values():
            if not v.inverse:
                errs.append(f"verb {v.name}: no inverse")
            errs += [f"verb {v.name}: unknown type {t}" for t in v.from_ + v.to if t not in known]
            errs += [f"verb {v.name}: replaces live verb {r}" for r in v.replaces if r in names]
            for c in v.columns:
                if c in seen_cols:
                    errs.append(f"column '{c}' maps to both {seen_cols[c]} and {v.name}")
                seen_cols[c] = v.name
        # Each reverse name must be unique, so every link can be read in exactly one direction.
        inverses = [v.inverse for v in self.verbs.values() if v.inverse]
        errs += [f"inverse {i} used twice" for i in {i for i in inverses if inverses.count(i) > 1}]
        errs += [f"inverse {i} is also a verb" for i in inverses if i in self.verbs]
        # An ID prefix must belong to one type only, or an ID would map to two different types.
        prefixes = [p for t in self.types.values() for p in t.id_prefixes]
        errs += [
            f"ID prefix {p} used twice" for p in {p for p in prefixes if prefixes.count(p) > 1}
        ]
        return errs

    # Build the lookup from ID prefix (such as FR) to type name, once and then reuse it.
    @cached_property
    def prefix_map(self) -> dict[str, str]:
        """ID prefix to type name."""
        return {p: t.name for t in self.types.values() for p in t.id_prefixes}

    # Build the lookup from normalised column header to verb name, once and then reuse it.
    @cached_property
    def column_map(self) -> dict[str, str]:
        """Normalized column header to verb name."""
        return {c: v.name for v in self.verbs.values() for c in v.columns}

    # Type for an ID prefix, if the vocabulary declares one. The lookup ignores case.
    def type_for_prefix(self, prefix: str) -> str | None:
        """Type for an ID prefix, if the vocabulary declares one."""
        return self.prefix_map.get(prefix.upper())

    # Build the lookup for tables without IDs: the first column's header maps to a type.
    @cached_property
    def keyless_map(self) -> dict[str, str]:
        """First-column header of an ID-less table to the type its rows become."""
        return {c: t.name for t in self.types.values() for c in t.keyless_columns}

    # Type named by a section heading. When several keywords match, the longest one wins.
    def type_for_heading(self, heading: str) -> str | None:
        """Best type for a section heading, by the longest matching keyword."""
        best, best_len = None, 0
        for s, e, name in self._keyword_spans(heading):
            if e - s > best_len:
                best, best_len = name, e - s
        return best

    # Find every whole-word keyword match in a heading, as (start, end, type name).
    def _keyword_spans(self, heading: str) -> list[tuple[int, int, str]]:
        """(start, end, type) of each type keyword in a heading.

        A keyword negated by "non" does not count: "Non-goals" names no goals, while
        "Non-functional requirements" is itself a keyword.
        """
        h = norm_header(heading)
        return [
            (m.start(), m.end(), t.name)
            for t in self.types.values()
            for k in map(norm_header, t.keywords)
            for m in re.finditer(rf"\b{re.escape(k)}\b", h)
            # Ignore a keyword that follows "non", so "Non-goals" does not count as goals.
            if not h[: m.start()].endswith("non ")
        ]

    # Return the one type a heading names, or None when it names none or several.
    def single_type_for_heading(self, heading: str) -> str | None:
        """The one type a heading names, or None when it names none or several.

        A keyword inside a longer matching keyword does not count ("requirements" inside
        "functional requirements"), so "Assumptions and Dependencies" names two types
        and "Out of Scope" names one.
        """
        spans = self._keyword_spans(heading)
        kept = {
            name
            for s, e, name in spans
            # Drop a keyword that sits inside a longer keyword match, so "requirements" inside
            # "functional requirements" is not counted twice.
            if not any(s2 <= s and e <= e2 and e2 - s2 > e - s for s2, e2, _ in spans)
        }
        return kept.pop() if len(kept) == 1 else None

    # Verb named by a column header, if the vocabulary declares one.
    def verb_for_column(self, header: str) -> str | None:
        """Verb for a column header, if the vocabulary declares one."""
        return self.column_map.get(norm_header(header))

    # Translate a vocabulary type name to the page type name stored in gbrain.
    def gbrain_type(self, vocab_type: str) -> str:
        """Page type name stored in gbrain for a vocabulary type."""
        return self.types[vocab_type].gbrain_type

    # Verbs whose target is a person (used to find who owns or approves what).
    def person_verbs(self) -> set[str]:
        """Verbs whose target is a person."""
        return {v.name for v in self.verbs.values() if v.target == "person"}

    # ---- reference document ---------------------------------------------------

    # Write the vocabulary reference (docs/VOCABULARY.md): every verb and page type, as tables.
    def render_markdown(self) -> str:
        """docs/VOCABULARY.md: every verb and page type, as tables."""

        # Format names as comma-separated inline code for the reference tables.
        def code(items: list[str]) -> str:
            return ", ".join(f"`{i}`" for i in items)

        # Escape pipes so text can sit inside a markdown table cell.
        def cell(text: str) -> str:
            return text.replace("|", "\\|")

        verbs = list(self.verbs.values())
        counts = {
            s: sum(v.status == s for v in verbs) for s in ("keep", "renamed", "merged", "new")
        }
        out = [
            "# docingest vocabulary",
            "",
            "Generated from `docingest/vocabulary/vocabulary.yaml` by "
            "`python -m docingest vocab`. "
            "Do not edit by hand: change the YAML and regenerate.",
            "",
            f"{len(verbs)} link verbs ({counts['keep']} kept, {counts['renamed']} renamed, "
            f"{counts['merged']} merged, {counts['new']} new) over {len(self.types)} page types. "
            "A link reads as a sentence: source, verb, target.",
            "",
            "## Link verbs",
            "",
            "| Group | Verb | Reverse | From | To | Meaning | Evidence | Docs | Status |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for v in verbs:
            status = v.status + (f" (was {code(v.replaces)})" if v.replaces else "")
            out.append(
                f"| {v.group} | `{v.name}` | `{v.inverse}` | {code(v.from_)} | {code(v.to)} "
                f"| {cell(v.description)} | {cell(v.evidence)} | {', '.join(v.docs)} | {status} |"
            )
        out += [
            "",
            "## Type groups",
            "",
            "| Group | Types |",
            "| --- | --- |",
        ]
        out += [f"| `{g}` | {code(m)} |" for g, m in self.type_groups.items()]
        out += [
            "",
            "## Page types",
            "",
            "| Group | Type | Folder | Scope | ID prefixes | Description |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for t in self.types.values():
            out.append(
                f"| {t.group} | `{t.name}` | `{t.dir}/` | {t.scope} | {code(t.id_prefixes)} "
                f"| {cell(t.description)} |"
            )
        return "\n".join(out) + "\n"

    # ---- gbrain schema pack ---------------------------------------------------

    # Write the gbrain schema pack (YAML): page types, link types and frontmatter link fields.
    def render_pack(self) -> str:
        """gbrain-schema-pack-v1 YAML. Block style only: gbrain's loader rejects
        inline {...} mappings."""
        p = self.pack
        out = [
            "# Generated by docingest from vocabulary.yaml. Do not edit by hand:",
            "# change vocabulary.yaml and re-run ingestion (or `python -m docingest pack`).",
            "api_version: gbrain-schema-pack-v1",
            f"name: {p['name']}",
            f"version: {p['version']}",
            f"description: {json.dumps(p['description'])}",  # JSON strings are valid YAML
            "gbrain_min_version: 0.60.0",
            f"extends: {p['extends']}",
            "",
            "page_types:",
        ]
        for t in self.types.values():
            # Built-in types already exist in gbrain, so they are not declared again in the pack.
            if t.builtin:
                continue
            out += [
                f"  - name: {t.gbrain_type}",
                f"    primitive: {t.primitive}",
                f"    path_prefixes: [{t.dir}/]",
            ]
            if t.extractable:
                out.append("    extractable: true")
        out += ["", "link_types:"]
        for v in self.verbs.values():
            out.append(f"  - name: {v.name}")
            if v.inverse:
                out.append(f"    inverse: {v.inverse}")
            if v.temporal:
                out.append(f"    temporal: {v.temporal}")
            if v.target:
                out += ["    inference:", f"      target_type: {self.gbrain_type(v.target)}"]
        # Every page type may carry every verb as a frontmatter field of the
        # same name; the extractor decides which fields a page actually has.
        out += ["", "frontmatter_links:"]
        for t in self.types.values():
            for v in self.verbs.values():
                out += [
                    f"  - page_type: {t.gbrain_type}",
                    f"    fields: [{v.name}]",
                    f"    link_type: {v.name}",
                ]
        return "\n".join(out) + "\n"
