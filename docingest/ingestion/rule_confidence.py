# What this file does: decides, for each section of a document, whether the rule-based
# extraction covered it completely or whether the text must go to the AI.
# The AI is only sent the text the rules could not read, which keeps cost and data sent low.

"""Per-section confidence in rule-based extraction, and routing to AI.

Rules are trusted only for content they demonstrably consumed: ID-keyed
tables, Field | Value tables and approval tables. Everything else in a section
is residual: prose, lists, tables the rules left as text, and long table cells
that name a known entity the row has no link to. A section skips the AI only
when its residual is negligible. When the router is unsure, it routes, so
accuracy never depends on it guessing right.

The text sent to the AI for a routed section is the residual, plus a one-line
placeholder for each table the rules already extracted. Tokens go only to
what the rules could not read.
"""

from __future__ import annotations

import re

from docingest.ingestion.extract_with_rules import ID_REF_RE, NAME_RE, NOT_PERSON
from docingest.utils.markdown_parsing import HEADING_RE, normalize_ws, tokens
from docingest.utils.data_types import DocumentResult

# Leftover text of at least this many words always goes to the AI.
MIN_RESIDUAL_WORDS = 20  # residual prose at or above this always routes
# Shorter leftover text still goes to the AI if it contains a cue word (such as "owner").
MIN_CUE_WORDS = 3  # shorter residual routes too, if it carries an extraction cue
LONG_CELL_WORDS = 12  # table cells this long are checked for unlinked mentions
CHARS_PER_TOKEN = 4  # rough, for estimates only

# Wording that usually signals an entity or relationship the rules cannot parse.
# Words that suggest a decision, owner, date, risk or requirement the rules may have missed.
CUE_RE = re.compile(
    r"\b(decided|decision|agreed|approved?|rejected|action items?|todo|to-do|follow[- ]up|"
    r"owner|owns|assigned|responsible|due|deadline|"
    r"by (?:mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|"
    r"will|must|shall|should|needs? to|requires?|depends? on|blocked|blocks|risks?|"
    r"issues?|concerns?|blockers?|mitigat\w+|milestones?|deliver\w*|accept\w*|kpi|metric|"
    r"target|budget|cost|invoice|payment|sla|scope|assum\w+|constraints?|question)\b|\?",
    re.I,
)
# Matches capitalised two- or three-word names in prose, such as "Jane Smith".
PERSON_IN_TEXT_RE = re.compile(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+){1,2}\b")


# Return a section's text with the parts the rules already read swapped for one-line
# placeholders. Returns (text, placeholders).
def _residual(res: DocumentResult, s, cells: list | None = None) -> tuple[str, list[str]]:
    """Section text minus what rules consumed, and placeholders for those parts.

    Consumed parts are tables the rules extracted and lists whose every item became an
    entity. cells: (table start, item id, column, text) for table cells the AI must still
    read; they are listed verbatim under their table's placeholder.
    """
    text, placeholders, out, pos = res.markdown, [], [], s.start
    # Find the parts of the section the rules consumed: extracted tables and fully extracted lists.
    spans = []  # (start, end, placeholder line, table start or None)
    for t in res.tables:
        if t.section == s.index and t.mapped_to != "text only":
            ids = [r.get(t.id_col, "") for r in t.rows if t.id_col and r.get(t.id_col)]
            span = f" ({ids[0]} … {ids[-1]})" if len(ids) > 1 else f" ({ids[0]})" if ids else ""
            line = f"[Table already extracted as {t.mapped_to}: {len(t.rows)} rows{span}]"
            spans.append((t.start, t.end, line, t.start))
    for b in res.consumed_lists:
        if b["section"] == s.index:
            line = f"[List already extracted: {b['items']} items]"
            spans.append((b["start"], b["end"], line, None))
    # Walk the consumed parts in order. Keep the text between them, and replace each one with
    # its placeholder.
    for start, end, line, tstart in sorted(spans, key=lambda x: x[0]):
        out.append(text[pos:start])
        placeholders.append(line)
        out.append(line + "\n")
        for cstart, item_id, col, cell in cells or []:
            if tstart is not None and cstart == tstart:
                out.append(f"{item_id} {col}: {cell}\n")
        pos = end
    out.append(text[pos : s.end])
    return "".join(out), placeholders


# Count the real words left in the residual, ignoring placeholders and headings.
def _content_words(residual: str, placeholders: list[str]) -> list[str]:
    body = residual
    for p in placeholders:
        body = body.replace(p, " ")
    lines = [line for line in body.splitlines() if not HEADING_RE.match(line)]
    return tokens("\n".join(lines))


# Decide for every section whether the rules covered it. Each decision lists its reasons,
# and a section goes to the AI ("ai") if it has any reason, otherwise it is "rules".
def route_sections(res: DocumentResult, globals_: list) -> list[dict]:
    """Decide, for every section, whether the rules fully covered it."""
    links: dict[str, set[str]] = {}
    for p in res.pages:
        links[p.slug] = {t for ts in p.links.values() for t in ts}
    for g in globals_:
        links.setdefault(g.slug, set()).update(t for ts in g.links.values() for t in ts)
    known_names = {g.slug: g.name for g in globals_ if len(g.name) >= 4}
    # Names of people already known from other documents. A known name in leftover prose is
    # a reason to route, because the rules may have missed a new fact about that person.
    known_people = {normalize_ws(g.name).lower() for g in globals_ if g.vocab_type == "person"}
    defined_ids = set(res.entity_ids)

    decisions = []
    for s in res.sections:
        residual, placeholders = _residual(res, s)
        words = _content_words(residual, placeholders)
        prose = "\n".join(
            line for line in residual.splitlines() if line.strip() not in placeholders
        )
        cues = sorted({m.group(0).lower() for m in CUE_RE.finditer(prose)})
        reasons = []

        if len(words) >= MIN_RESIDUAL_WORDS:
            reasons.append(f"{len(words)} words of prose or list text the rules do not parse")
        elif len(words) >= MIN_CUE_WORDS and cues:
            reasons.append(f"short text with extraction cues ({', '.join(cues[:5])})")

        unmapped = [t for t in res.tables if t.section == s.index and t.mapped_to == "text only"]
        if unmapped:
            reasons.append(
                f"{len(unmapped)} table(s) without ID or field columns "
                f"({sum(len(t.rows) for t in unmapped)} rows) left as text"
            )

        # Long cells in extracted tables that name a known entity the row does not link to.
        unlinked, cells = [], []
        for t in res.tables:
            if t.section != s.index or not t.id_col:
                continue
            for row in t.rows:
                slug = res.entity_ids.get(row.get(t.id_col, "").strip())
                if not slug:
                    continue
                for col, cell in row.items():
                    if col == t.id_col or len(cell.split()) < LONG_CELL_WORDS:
                        continue
                    names = [
                        name
                        for target, name in known_names.items()
                        if target != slug
                        and target not in links.get(slug, set())
                        and re.search(rf"\b{re.escape(name)}\b", cell, re.I)
                    ]
                    if names:
                        unlinked.append(f"{row[t.id_col]} {col} mentions {', '.join(names)}")
                        cells.append([t.start, row[t.id_col], col, cell])
        # Report the flagged cells. The AI later receives only these cells, not the whole table.
        if unlinked:
            reasons.append(
                f"{len(unlinked)} long table cell(s) name an entity without a link "
                f"(e.g. {unlinked[0]})"
            )

        undefined = sorted(
            {
                r
                for r in ID_REF_RE.findall(s.text(res.markdown))
                if r in res.undefined_refs and r not in defined_ids
            }
        )
        if undefined:
            reasons.append(f"references undefined IDs {', '.join(undefined[:5])}")

        for g in res.gaps:
            # Only schema fallbacks in this section count. A verb mismatch is not a reason to
            # route.
            if g["section"] != s.index or g["kind"] == "verb_mismatch":
                continue
            if g["kind"] == "unknown_prefix":
                reasons.append(
                    f"schema fallback: {len(g['rows'])} row(s) with unknown ID prefix "
                    f"{g['prefix']}- typed record"
                )
            else:
                reasons.append(
                    f"schema fallback: column \"{g['column']}\" has no verb "
                    f"({len(g['rows'])} generic references link(s))"
                )

        new_people = sorted(
            {
                n
                for n in PERSON_IN_TEXT_RE.findall(prose)
                if NAME_RE.match(n) and not NOT_PERSON.search(n) and n.lower() in known_people
            }
        )
        # Last check: a known person named in short prose routes the section if nothing else did.
        if new_people and not reasons:
            reasons.append(f"short text names {', '.join(new_people[:3])}")

        total = len(tokens(s.text(res.markdown)))
        # Rough share of the section that the rules covered, from 0 to 1, for the run report.
        coverage = 1.0 if total == 0 else max(0.0, 1 - len(words) / total)
        decisions.append(
            {
                "section": s.index + 1,
                "heading": s.heading,
                "route": "ai" if reasons else "rules",
                "confidence": "low" if reasons else "high",
                "rule_coverage": round(coverage, 3),
                "residual_words": len(words),
                "reasons": reasons or ["fully consumed by table rules"],
                "residual_chars": len(residual) + sum(len(c[3]) + 20 for c in cells),
                "section_chars": s.end - s.start,
                "cells": cells,
            }
        )
    return decisions


# The exact text the AI receives for one routed section.
def ai_input(res: DocumentResult, decision: dict) -> str:
    """Text the AI sees for a routed section: residual, table placeholders, flagged cells."""
    residual, _ = _residual(res, res.sections[decision["section"] - 1], decision.get("cells"))
    return residual


# Estimate the tokens and cost of two plans: hybrid (rules first, AI only where routed) and
# all sections sent to the AI. Used for the plan command, before any money is spent.
def estimate(
    res: DocumentResult, decisions: list[dict], system_chars: int, known_chars: int
) -> dict:
    """Rough token and cost estimates for hybrid routing versus sending every section."""
    from docingest.ingestion.extract_with_ai import MIN_SECTION_CHARS, PRICE_IN, PRICE_OUT

    # Cost of sending the given sections: input tokens include the system prompt and known
    # entities on every call, and output is a rough guess from the input size.
    def cost(sections: list[tuple[int, int]]) -> dict:
        n = len(sections)
        tin = (
            sum(chars for chars, _ in sections) // CHARS_PER_TOKEN
            + n * (system_chars + known_chars) // CHARS_PER_TOKEN
        )
        tout = sum(max(300, chars // CHARS_PER_TOKEN // 3) for chars, _ in sections)
        return {
            "sections": n,
            "input_tokens": tin,
            "output_tokens": tout,
            "cost_usd": round(tin * PRICE_IN / 1e6 + tout * PRICE_OUT / 1e6, 4),
        }

    every = [(d["section_chars"], 0) for d in decisions if d["section_chars"] >= MIN_SECTION_CHARS]
    hybrid = [(d["residual_chars"], 0) for d in decisions if d["route"] == "ai"]
    return {"hybrid": cost(hybrid), "all_sections": cost(every)}
