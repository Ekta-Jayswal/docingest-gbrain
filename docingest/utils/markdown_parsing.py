# What this file does: reads markdown text and finds its structure.
# It makes slugs, tokens for comparing text, headings, sections, pipe tables and lists.
# The rule-based extraction in ingestion/ builds on these helpers.

"""Markdown helpers: slugs, sections, tables, tokens."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from docingest.utils.data_types import ListBlock, Section, Table

# Matches an ATX heading line (# to ######). Group 1 is the #s, group 2 the heading text.
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
LIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?P<bullet>[-*+•])|(?P<number>\d{1,3}|[a-z])[.)])[ \t]+(?P<text>\S.*)$"
)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
# Matches the separator row of a pipe table, such as |---|:---:|.
SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
TOKEN_RE = re.compile(r"[0-9a-z]+")

# Target size of each piece when a document has no headings to split on.
SEGMENT_CHARS = 3000  # target size when a document has no headings


# Turn any text into a URL-safe id: ASCII letters and digits joined by hyphens.
def slugify(text: str, max_len: int = 60) -> str:
    """ASCII, lower-case, hyphen-separated slug of at most ``max_len`` characters."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "untitled"


# Split text into lower-case ASCII words, used to compare the text of two documents.
def tokens(text: str) -> list[str]:
    """Lower-case ASCII word tokens of a text."""
    text = unicodedata.normalize("NFKD", text)
    # Dashes, quotes and other symbols separate words ("November–December"). Dropping them
    # would glue the words together, and no search index contains the glued word.
    text = "".join(" " if unicodedata.category(c)[0] in "PSZ" else c for c in text)
    return TOKEN_RE.findall(text.encode("ascii", "ignore").decode().lower())


# How much of the reference text's words also appear in the candidate.
# Returns the share (0 to 1) and up to 15 of the words that are missing.
def token_coverage(reference: str, candidate: str) -> tuple[float, list[str]]:
    """Share of reference tokens (as a multiset) present in candidate."""
    ref, cand = Counter(tokens(reference)), Counter(tokens(candidate))
    total = sum(ref.values())
    if total == 0:
        return 1.0, []
    missing = ref - cand
    return 1 - sum(missing.values()) / total, [t for t, _ in missing.most_common(15)]


# Make whitespace uniform, so two texts that differ only in spacing compare equal.
def normalize_ws(text: str) -> str:
    """Collapse every run of whitespace to one space and trim."""
    return re.sub(r"\s+", " ", text).strip()


# Pair each line with its character offset in the document, so a line can be placed
# in the text again.
def _line_offsets(markdown: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for line in markdown.splitlines(keepends=True):
        out.append((pos, line))
        pos += len(line)
    return out


# Find every heading outside fenced code blocks. Returns (offset, level, text) for each.
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


# Cut the document into sections that together cover all of it, with no gaps.
# Used by the rest of the pipeline to work one section at a time.
def split_sections(markdown: str, segment_label: str = "Part") -> list[Section]:
    """Partition the markdown into sections whose texts concatenate back to it exactly.

    Splits at headings of the shallowest level that occurs at least twice, at the
    level below it (subsections such as "3.2"), and at every shallower heading.
    Without usable headings, splits into segments of about SEGMENT_CHARS at line
    boundaries.
    """
    hs = headings(markdown)
    levels = Counter(level for _, level, _ in hs)
    split_level = next((lvl for lvl in sorted(levels) if levels[lvl] >= 2), None)
    if split_level is None and hs:
        split_level = min(levels)
    cuts = [
        (pos, level, text) for pos, level, text in hs if split_level and level <= split_level + 1
    ]

    # If there are usable headings, cut at them. Otherwise fall back to fixed-size pieces.
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
    # A blank opening section is merged into the next one, so the sections still cover the
    # whole document exactly.
    if len(sections) > 1 and not markdown[sections[0].start : sections[0].end].strip():
        sections[1].start = 0
        sections.pop(0)
        for i, s in enumerate(sections):
            s.index = i
    if not sections:
        sections = [Section(0, "Content", 0, 0, len(markdown))]
    return sections


# Cut a document with no headings into pieces of about SEGMENT_CHARS, preferring to cut
# at blank lines.
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


# Split one pipe-table row into its cells. Escaped pipes (\|) stay inside their cell.
def split_cells(line: str) -> list[str]:
    """Cells of one pipe-table row, honouring escaped pipes."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in CELL_SPLIT_RE.split(line)]


# Find every pipe table in the document, with the section it sits in and its offset.
def parse_tables(markdown: str, sections: list[Section]) -> list[Table]:
    """Every pipe table, with its section and offset."""
    tables: list[Table] = []
    lines = _line_offsets(markdown)
    i = 0
    while i < len(lines):
        pos, line = lines[i]
        # A table is a row with pipes, followed directly by a separator row such as |---|---|.
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


# Indentation width of a line, with tabs counted as 4 spaces.
def _indent(text: str) -> int:
    return len(text.expandtabs(4)) - len(text.expandtabs(4).lstrip())


# Find every bulleted or numbered list, with its top-level items, the heading above it
# and the line just before it.
def parse_lists(markdown: str, sections: list[Section]) -> list[ListBlock]:
    """Every bulleted or numbered list, with its top-level items, heading and lead-in line.

    Items may be separated by blank lines. A deeper-indented item is a sub-item (not
    returned); an indented plain line continues the item above it. A list ends at a
    heading, a table or any other unindented line.
    """
    blocks: list[ListBlock] = []
    heading, lead_in, in_fence = "", "", False
    cur: ListBlock | None = None
    base = 0
    blank_before = False

    # Finish the list being read, if it has any items, and record where it ends.
    def close(pos: int) -> None:
        nonlocal cur
        if cur and cur.items:
            cur.end = pos
            blocks.append(cur)
        cur = None

    for pos, line in _line_offsets(markdown):
        text = line.rstrip("\n")
        if FENCE_RE.match(text):
            close(pos)
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        h = HEADING_RE.match(text)
        if h:
            close(pos)
            heading, lead_in = h.group(2).strip(), ""
            continue
        item = LIST_ITEM_RE.match(text)
        # A list item is a line starting with a bullet or number. Table rows and lines of only
        # dashes or stars are not items.
        if item and not text.lstrip().startswith("|") and item.group("text").strip("*-_ "):
            depth = _indent(item.group("indent"))
            if cur is None:
                section = next(s.index for s in sections if s.start <= pos < s.end)
                cur = ListBlock(
                    section=section,
                    start=pos,
                    end=pos,
                    ordered=item.group("number") is not None,
                    items=[],
                    heading=heading,
                    lead_in=lead_in,
                )
                base = depth
            # Keep top-level items only. A deeper-indented line is a sub-item and is skipped.
            if depth <= base + 1:
                cur.items.append(item.group("text").strip())
            blank_before = False
            continue
        if not text.strip():
            blank_before = True
            continue
        # An indented line straight after an item continues that item's text.
        if cur and _indent(text) > base and not blank_before:
            cur.items[-1] += " " + text.strip()
            continue
        if cur and _indent(text) > base + 1:
            continue  # an indented paragraph inside a sub-item
        close(pos)
        lead_in = text.strip()
        blank_before = False
    close(len(markdown))
    return blocks


# Build a markdown pipe table from a header and rows. Used to write tables into pages.
def md_table(header: list[str], rows: list[list[str]]) -> str:
    """Render a header and rows as a markdown pipe table."""

    # Make one value safe for a table cell: escape pipes and turn line breaks into <br>.
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
