# What this file does: defines the data classes passed between pipeline steps.
# Each class describes one shape of data (a converted file, a section, a table, a list,
# a page, the final result), so every step agrees on what it receives.

"""Data model shared by conversion, extraction, writing, and verification."""

from __future__ import annotations

from dataclasses import dataclass, field


# Output of the conversion step: the document as markdown, plus how it was converted.
# kind_hint is a guess at the document type (email, transcript, ...) for later steps.
@dataclass
class Converted:
    """Result of converting one file: markdown plus converter metadata."""

    markdown: str
    converter: str
    kind_hint: str | None = None  # email | transcript | chat | spreadsheet | presentation
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    attachments: list = field(default_factory=list)  # [Path] saved attachment files


# One part of a document, from one heading to the next.
# start and end are character offsets into the markdown, so section.text(markdown)
# returns this part of the document.
@dataclass
class Section:
    """A slice of the document markdown, starting at a heading."""

    index: int
    heading: str
    level: int
    start: int  # character offsets into the document markdown
    end: int

    # Slice this section's text out of the full document markdown using its offsets.
    def text(self, markdown: str) -> str:
        """This section's text within the document markdown."""
        return markdown[self.start : self.end]


# A pipe table found in the markdown: its header, rows and position.
# mapped_to records what extraction did with it, for the run report.
@dataclass
class Table:
    """A pipe table found in the markdown, and what extraction did with it."""

    section: int
    start: int
    header: list[str]
    rows: list[dict[str, str]]
    mapped_to: str = "text only"  # what the extractor did with it (for the report)
    id_col: str | None = None
    end: int = 0  # offset just past the last row


# A bulleted or numbered list: its top-level items and the context around it.
# heading and lead_in show the question the list answers.
@dataclass
class ListBlock:
    """A bulleted or numbered list found in the markdown: its top-level items and context."""

    section: int
    start: int
    end: int
    ordered: bool
    items: list[str]
    heading: str  # nearest heading above the list, at any level
    lead_in: str = ""  # the line just before the list ("The project does not include:")


# One gbrain page to write: its slug (unique id), type, title, fields, links and body.
@dataclass
class Page:
    """One gbrain page to write: slug, type, title, attributes, links and body."""

    slug: str
    type: str  # gbrain page type
    title: str
    fields: dict = field(default_factory=dict)  # scalar attributes
    links: dict[str, list[str]] = field(default_factory=dict)  # verb -> target slugs
    body: str = ""

    # Add a typed link (verb -> target slug). Duplicate links and self-links are skipped.
    def add_link(self, verb: str, target: str) -> None:
        """Add a link once; self-links are ignored."""
        targets = self.links.setdefault(verb, [])
        if target not in targets and target != self.slug:
            targets.append(target)


# What one document says about a shared entity (a stakeholder, system or term that many
# documents can mention). Contributions from all documents are merged later, so each
# shared entity is written once.
@dataclass
class GlobalContribution:
    """What one document says about a shared (global) entity."""

    slug: str
    vocab_type: str
    name: str
    attributes: dict = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # body lines for this document

    # Add a link once. Same as Page.add_link, but without the self-link check.
    def add_link(self, verb: str, target: str) -> None:
        """Add a link once."""
        targets = self.links.setdefault(verb, [])
        if target not in targets:
            targets.append(target)


# Everything extracted from one document. This is what gets saved to the document's
# manifest (its per-document record) for the run.
@dataclass
class DocumentResult:
    """Everything extracted from one document, as written to its manifest."""

    key: str
    source_path: str
    sha256: str
    kind: str
    title: str
    markdown: str
    converter: str
    sections: list[Section]
    tables: list[Table]
    pages: list[Page]
    globals: list[GlobalContribution]
    warnings: list[str] = field(default_factory=list)
    reference_text: str = ""  # independent text extraction, for the coverage check
    meta: dict = field(default_factory=dict)
    entity_ids: dict[str, str] = field(default_factory=dict)  # item id -> slug
    undefined_refs: list[str] = field(default_factory=list)
    llm_report: dict = field(default_factory=dict)
    routing: list[dict] = field(default_factory=list)  # per-section rule confidence / AI routing
    consumed_lists: list[dict] = field(default_factory=list)  # lists whose items the rules extracted
    gaps: list[dict] = field(default_factory=list)  # fallbacks the vocabulary did not cover
