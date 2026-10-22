# What this file does: writes each page as a gbrain markdown file, with YAML frontmatter
# (the header block with type, title and links) followed by the body text.

"""Write pages as gbrain markdown files (frontmatter + body)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from docingest.utils.data_types import Page


# Turn one value into a YAML-safe string. JSON quoting works as YAML, so use it.
def _scalar(value) -> str:
    # JSON scalars are valid YAML and quote safely.
    return json.dumps(value, ensure_ascii=False)


# Build the text of one page file: the frontmatter, then a blank line, then the body.
def page_markdown(page: Page) -> str:
    """A page as gbrain markdown: YAML frontmatter (attributes and links) and body."""
    out = ["---", f"type: {_scalar(page.type)}", f"title: {_scalar(page.title)}"]
    for key, value in [*page.fields.items(), *page.links.items()]:
        # Leave out empty fields and empty links so the frontmatter only holds real values.
        if value is None or value in ("", []):
            continue
        if isinstance(value, list):
            out.append(f"{key}:")
            out.extend(f"  - {_scalar(v)}" for v in value)
        else:
            out.append(f"{key}: {_scalar(value)}")
    out.append("---")
    return "\n".join(out) + "\n\n" + page.body


# Write all pages under root, one file per slug. The old output is deleted first, so
# pages that no longer exist do not stay behind.
def write_pages(pages: list[Page], root: Path) -> None:
    """Write pages under ``root`` (one file per slug), replacing previous output."""
    if root.exists():
        shutil.rmtree(root)
    for page in pages:
        path = root / f"{page.slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page_markdown(page))
