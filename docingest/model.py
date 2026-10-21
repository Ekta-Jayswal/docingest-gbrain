"""Data model shared by conversion, extraction, writing, and verification."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Converted:
    """Result of converting one file: markdown plus converter metadata."""

    markdown: str
    converter: str
    kind_hint: str | None = None  # email | transcript | chat | spreadsheet | presentation
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    attachments: list = field(default_factory=list)  # [Path] saved attachment files


@dataclass
class Section:
    """A slice of the document markdown, starting at a heading."""

    index: int
    heading: str
    level: int
    start: int  # character offsets into the document markdown
    end: int

    def text(self, markdown: str) -> str:
        """This section's text within the document markdown."""
        return markdown[self.start : self.end]


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


@dataclass
class Page:
    """One gbrain page to write: slug, type, title, attributes, links and body."""

    slug: str
    type: str  # gbrain page type
    title: str
    fields: dict = field(default_factory=dict)  # scalar attributes
    links: dict[str, list[str]] = field(default_factory=dict)  # verb -> target slugs
    body: str = ""

    def add_link(self, verb: str, target: str) -> None:
        """Add a link once; self-links are ignored."""
        targets = self.links.setdefault(verb, [])
        if target not in targets and target != self.slug:
            targets.append(target)


@dataclass
class GlobalContribution:
    """What one document says about a shared (global) entity."""

    slug: str
    vocab_type: str
    name: str
    attributes: dict = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # body lines for this document

    def add_link(self, verb: str, target: str) -> None:
        """Add a link once."""
        targets = self.links.setdefault(verb, [])
        if target not in targets:
            targets.append(target)


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
