"""Write pages as gbrain markdown files (frontmatter + body)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .model import Page


def _scalar(value) -> str:
    # JSON scalars are valid YAML and quote safely.
    return json.dumps(value, ensure_ascii=False)


def page_markdown(page: Page) -> str:
    """A page as gbrain markdown: YAML frontmatter (attributes and links) and body."""
    out = ["---", f"type: {_scalar(page.type)}", f"title: {_scalar(page.title)}"]
    for key, value in [*page.fields.items(), *page.links.items()]:
        if value is None or value in ("", []):
            continue
        if isinstance(value, list):
            out.append(f"{key}:")
            out.extend(f"  - {_scalar(v)}" for v in value)
        else:
            out.append(f"{key}: {_scalar(value)}")
    out.append("---")
    return "\n".join(out) + "\n\n" + page.body


def write_pages(pages: list[Page], root: Path) -> None:
    """Write pages under ``root`` (one file per slug), replacing previous output."""
    if root.exists():
        shutil.rmtree(root)
    for page in pages:
        path = root / f"{page.slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page_markdown(page))
