# What this file does: remembers what has been ingested, between runs.
# It records each document's pages (so stale pages can be removed) and what each document
# says about shared entities, so a shared page is rebuilt from every document that mentions it.

"""Persistent ingestion state.

Tracks every ingested document (its pages, for stale-page cleanup) and the
per-document contributions to shared entities (people, organizations,
systems, glossary terms), so a shared page can be rebuilt from every
document that mentions it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from docingest.utils import clock
from docingest.utils.data_types import DocumentResult, Page
from docingest.vocabulary.vocabulary_loader import Vocabulary

# Version of the state file format. Change it if the saved layout changes.
STATE_VERSION = 1


# The saved ingestion state, kept in work/state.json.
class State:
    """Ingestion state stored in ``work/state.json``."""

    # Load the state file if it exists, otherwise start empty.
    def __init__(self, path: Path):
        self.path = path
        self.data = {"version": STATE_VERSION, "documents": {}, "globals": {}}
        if path.exists():
            self.data = json.loads(path.read_text())

    # Write the state to a temporary file first, then rename it over the real file. A crash
    # part-way through then leaves the old state intact.
    def save(self) -> None:
        """Write the state atomically (temporary file, then rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    # The documents recorded in the state, keyed by document key.
    @property
    def documents(self) -> dict:
        """Ingested documents by key."""
        return self.data["documents"]

    # Keep a source file's key stable across runs. A new file gets the proposed key, or the
    # proposed key with a number added if that key is already taken.
    def key_for(self, source_path: str, proposed: str) -> str:
        """The key already assigned to a source file, or a new unique key based on ``proposed``."""
        for key, doc in self.documents.items():
            if doc["source_path"] == source_path:
                return key
        key, n = proposed, 2
        while key in self.documents:
            key, n = f"{proposed}-{n}", n + 1
        return key

    # Save one document's result and its shared-entity contributions. Returns the slugs this
    # document produced last time but not now, so they can be deleted.
    def record_document(self, res: DocumentResult, pages: list[Page]) -> list[str]:
        """Store the document; return slugs it produced before but no longer does."""
        previous = set(self.documents.get(res.key, {}).get("pages", []))
        current = [p.slug for p in pages]
        self.documents[res.key] = {
            "source_path": res.source_path,
            "sha256": res.sha256,
            "kind": res.kind,
            "title": res.title,
            "pages": current,
            "ingested_at": clock.iso(),
        }
        # Remove this document's old contributions first, so re-ingesting it does not double them.
        for entry in self.data["globals"].values():
            entry["contributions"].pop(res.key, None)
        for g in res.globals:
            entry = self.data["globals"].setdefault(
                g.slug, {"vocab_type": g.vocab_type, "name": g.name, "contributions": {}}
            )
            entry["contributions"][res.key] = {
                "attributes": g.attributes,
                "links": g.links,
                "notes": g.notes,
            }
        return sorted(previous - set(current))

    # Build one page per shared entity from all its contributions. Returns (pages, orphans),
    # where orphans are shared entities that no document mentions any more.
    def global_pages(self, vocab: Vocabulary) -> tuple[list[Page], list[str]]:
        """Shared entity pages built from every contributing document, plus slugs of
        shared entities no document mentions any more (to delete)."""
        pages, orphans = [], []
        for slug, entry in sorted(self.data["globals"].items()):
            # Sort contributions by document key, so the same input always gives the same page.
            contribs = dict(
                sorted(entry["contributions"].items())
            )  # stable order: same input, same page
            if not contribs:
                orphans.append(slug)
                continue
            page = Page(
                slug=slug,
                type=vocab.gbrain_type(entry["vocab_type"]),
                title=entry["name"],
                fields={
                    "name": entry["name"],
                    "entity_type": entry["vocab_type"],
                    "document_count": len(contribs),
                },
            )
            merged: dict[str, list] = {}
            for c in contribs.values():
                # Merge attributes from all documents, skipping duplicate values.
                for k, v in c["attributes"].items():
                    for item in v if isinstance(v, list) else [v]:
                        if item not in merged.setdefault(k, []):
                            merged[k].append(item)
                for verb, targets in c["links"].items():
                    for t in targets:
                        page.add_link(verb, t)
            # A single value is stored as a plain field. Several values, or any email, stay a list.
            for k, values in merged.items():
                page.fields[k] = values[0] if len(values) == 1 and k != "email" else values
            lines = [
                f"# {entry['name']}",
                "",
                f"Shared {entry['vocab_type'].replace('_', ' ')} referenced by "
                f"{len(contribs)} document(s).",
                "",
            ]
            # Add a section to the body for each document, so the reader can see where each note
            # came from.
            for key, c in contribs.items():
                title = self.documents.get(key, {}).get("title", key)
                lines += [f"## In {title} ({key})", "", *c["notes"], ""]
            page.body = "\n".join(lines).rstrip() + "\n"
            pages.append(page)
        for slug in orphans:
            del self.data["globals"][slug]
        return pages, orphans
