"""Markdown helpers: slugs, sections, tables, tokens."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .model import Section, Table

HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
TOKEN_RE = re.compile(r"[0-9a-z]+")

SEGMENT_CHARS = 3000  # target size when a document has no headings


def slugify(text: str, max_len: int = 60) -> str:
    """ASCII, lower-case, hyphen-separated slug of at most ``max_len`` characters."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "untitled"


def tokens(text: str) -> list[str]:
    """Lower-case ASCII word tokens of a text."""
    text = unicodedata.normalize("NFKD", text)
    # Dashes, quotes and other symbols separate words ("November–December"). Dropping them
    # would glue the words together, and no search index contains the glued word.
    text = "".join(" " if unicodedata.category(c)[0] in "PSZ" else c for c in text)
    return TOKEN_RE.findall(text.encode("ascii", "ignore").decode().lower())


def token_coverage(reference: str, candidate: str) -> tuple[float, list[str]]:
    """Share of reference tokens (as a multiset) present in candidate."""
    ref, cand = Counter(tokens(reference)), Counter(tokens(candidate))
    total = sum(ref.values())
    if total == 0:
        return 1.0, []
    missing = ref - cand
    return 1 - sum(missing.values()) / total, [t for t, _ in missing.most_common(15)]


def normalize_ws(text: str) -> str:
    """Collapse every run of whitespace to one space and trim."""
    return re.sub(r"\s+", " ", text).strip()


def _line_offsets(markdown: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for line in markdown.splitlines(keepends=True):
        out.append((pos, line))
        pos += len(line)
    return out


def headings(markdown: str) -> list[tuple[int, int, str]]:
    """(offset, level, text) for ATX headings outside fenced code."""
    found, in_fence = [], False
    for pos, line in _line_offsets(markdown):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            m = HEADING_RE.match(line.rstrip("\n"))
            if m:
                found.append((pos, len(m.group(1)), m.group(2).strip()))
    return found


def split_sections(markdown: str, segment_label: str = "Part") -> list[Section]:
    """Partition the markdown into sections whose texts concatenate back to it exactly.

    Splits at headings of the shallowest level that occurs at least twice (and
    every shallower heading). Without usable headings, splits into segments of
    about SEGMENT_CHARS at line boundaries.
    """
    hs = headings(markdown)
    levels = Counter(level for _, level, _ in hs)
    split_level = next((lvl for lvl in sorted(levels) if levels[lvl] >= 2), None)
    if split_level is None and hs:
        split_level = min(levels)
    cuts = [(pos, level, text) for pos, level, text in hs if split_level and level <= split_level]

    if cuts:
        bounds = [(0, 0, "Preamble")] if cuts[0][0] > 0 else []
        bounds += cuts
    else:
        bounds = _segment_bounds(markdown, segment_label)

    sections: list[Section] = []
    for i, (pos, level, text) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else len(markdown)
        sections.append(
            Section(index=len(sections), heading=text, level=level, start=pos, end=end)
        )
    # A whitespace-only preamble merges into the next section (keeps the partition exact).
    if len(sections) > 1 and not markdown[sections[0].start : sections[0].end].strip():
        sections[1].start = 0
        sections.pop(0)
        for i, s in enumerate(sections):
            s.index = i
    if not sections:
        sections = [Section(0, "Content", 0, 0, len(markdown))]
    return sections


def _segment_bounds(markdown: str, label: str) -> list[tuple[int, int, str]]:
    bounds, seg_start, n = [(0, 0, f"{label} 1")], 0, 1
    for pos, line in _line_offsets(markdown):
        size = pos - seg_start
        if size >= SEGMENT_CHARS and (
            not line.strip() or label != "Part" or size >= 2 * SEGMENT_CHARS
        ):
            n += 1
            bounds.append((pos, 0, f"{label} {n}"))
            seg_start = pos
    return bounds


def split_cells(line: str) -> list[str]:
    """Cells of one pipe-table row, honouring escaped pipes."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT_RE.split(line)]


def parse_tables(markdown: str, sections: list[Section]) -> list[Table]:
    """Every pipe table, with its section and offset."""
    tables: list[Table] = []
    lines = _line_offsets(markdown)
    i = 0
    while i < len(lines):
        pos, line = lines[i]
        if "|" in line and i + 1 < len(lines) and SEP_RE.match(lines[i + 1][1].strip()):
            header = split_cells(line)
            rows, j = [], i + 2
            while j < len(lines) and "|" in lines[j][1] and lines[j][1].strip():
                cells = split_cells(lines[j][1])
                cells += [""] * (len(header) - len(cells))
                rows.append(
                    dict(zip(header, cells, strict=False))
                )  # extra cells beyond the header are dropped
                j += 1
            section = next(s.index for s in sections if s.start <= pos < s.end)
            end = lines[j][0] if j < len(lines) else len(markdown)
            tables.append(Table(section=section, start=pos, header=header, rows=rows, end=end))
            i = j
        else:
            i += 1
    return tables


def md_table(header: list[str], rows: list[list[str]]) -> str:
    """Render a header and rows as a markdown pipe table."""

    def cell(v) -> str:
        return (
            str(v if v is not None else "")
            .replace("|", "\\|")
            .replace("\r", "")
            .replace("\n", "<br>")
            .strip()
        )

    out = [
        "| " + " | ".join(cell(h) for h in header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for r in rows:
        padded = list(r) + [""] * (len(header) - len(r))
        out.append("| " + " | ".join(cell(v) for v in padded[: len(header)]) + " |")
    return "\n".join(out)
