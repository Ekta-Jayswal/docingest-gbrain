# What this file does: turns bold-only lines in Word-converted documents into real headings.
# Without this, the section splitter would see no headings at all in many documents.

"""Turn bold pseudo-headings into real markdown headings.

Documents converted from Word often mark headings only with bold text
(``**3.2 Business Objectives**``), so the section splitter sees no headings at all.
When a document has fewer than two real headings, this step rewrites:

- a line that is entirely bold and starts with a section number into a heading whose
  level follows the number's depth (``1.`` -> ``##``, ``1.1`` -> ``###``, ...);
- the first entirely bold line naming the document kind ("Business Requirements
  Document", "Statement of Work", ...) into the ``#`` title.

Only the markers change; every word stays, so the conversion check still sees 100% of
the source words. Documents that already use markdown headings are left untouched.
"""

from __future__ import annotations

import re

from docingest.utils.markdown_parsing import FENCE_RE, headings

BOLD_LINE_RE = re.compile(r"^\s*(\*\*|__)(?P<text>[^*_].*?)\1\s*$")
NUMBERED_RE = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")
TITLE_PHRASES = (
    "business requirements document",
    "product requirements document",
    "statement of work",
    "scope of work",
    "functional specification",
    "technical specification",
    "requirements specification",
)


# Rewrite bold pseudo-headings as markdown headings. Returns (markdown, number of lines changed).
# Documents that already have two or more headings are returned untouched.
def normalize_headings(markdown: str) -> tuple[str, int]:
    """The markdown with bold pseudo-headings made real, and how many lines changed."""
    # The document already has real headings, so there is nothing to fix.
    if len(headings(markdown)) >= 2:
        return markdown, 0
    out, changed, in_fence, has_title = [], 0, False, False
    for line in markdown.splitlines(keepends=True):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        # Skip code blocks and table rows. Only a bold line that is the whole line counts.
        m = None if in_fence or "|" in line else BOLD_LINE_RE.match(line)
        if m:
            text = m.group("text").strip()
            num = NUMBERED_RE.match(text)
            ending = "\n" if line.endswith("\n") else ""
            if num:
                # Heading level from the number's depth: "1" becomes ##, "1.1" becomes ###, and so
                # on, capped at the deepest markdown level (######).
                level = min(1 + len(num.group("num").split(".")), 6)
                out.append(f"{'#' * level} {text}{ending}")
                changed += 1
                continue
            # The first bold line that names the document type becomes the # title.
            if not has_title and text.lower().rstrip(":") in TITLE_PHRASES:
                out.append(f"# {text}{ending}")
                has_title = True
                changed += 1
                continue
        out.append(line)
    return "".join(out), changed
