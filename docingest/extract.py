"""Rule-based extraction: document, section, and entity pages from markdown.

Always runs, needs no API key. Produces:
  - one source_document page per input file
  - one document_section page per section; together they hold the full text
  - entity pages for rows of ID-keyed tables (FR-001, RK-02, ...)
  - shared person / organization / system / glossary entities
  - links from table columns, document-control fields, approvals, email
    headers, and transcript speakers
"""

from __future__ import annotations

import re
from pathlib import Path

from .mdutil import normalize_ws, parse_tables, slugify, split_sections
from .model import Converted, DocumentResult, GlobalContribution, Page, Table
from .vocab import Vocabulary, norm_header

ID_CELL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}-\d{1,5}[A-Z]?$")
ID_REF_RE = re.compile(r"\b[A-Z][A-Z0-9]{0,7}-\d{1,5}[A-Z]?\b")
ID_HEADERS = {"id", "ref", "reference", "ref no", "key", "no", "item id", "req id", "number"}
FIELD_HEADERS = {"field", "property", "attribute", "item", "key", "parameter"}
VALUE_HEADERS = {"value", "details", "description", "entry"}
APPROVAL_HEADERS = {
    "decision",
    "approval",
    "approval status",
    "approved",
    "sign off",
    "signoff",
    "signature",
}
APPROVER_HEADERS = {"stakeholder", "approver", "name", "signatory", "approved by", "reviewer"}
NAME_RE = re.compile(r"^[A-Z][a-zA-Z.'\-]+(?: [A-Z][a-zA-Z.'\-]+){0,3}$")
NOT_PERSON = re.compile(
    r"\b(team|department|group|office|committee|board|all|tbd|n/?a|none)\b", re.I
)
# Attribute keys gbrain reads as links or reserves; renamed to avoid accidental edges.
RESERVED_KEYS = {
    "type",
    "title",
    "slug",
    "tags",
    "company",
    "companies",
    "founded",
    "key_people",
    "partner",
    "investors",
    "lead",
    "attendees",
    "sources",
    "source",
    "related",
    "see_also",
}

DOC_DIR = "docs/documents"
SECTION_DIR = "docs/sections"


def person_name_from_address(name: str, addr: str) -> str:
    """A person's display name from an email header, or one derived from the address."""
    if name and "@" not in name:
        return normalize_ws(name.strip("'\" "))
    local = (addr or name).split("@")[0]
    return " ".join(p.capitalize() for p in re.split(r"[._\-+]+", local) if p) or addr


def document_id(markdown: str) -> str | None:
    """The document's own ID from a Field | Value table ('Document ID'), if any."""
    sections = split_sections(markdown)
    for t in parse_tables(markdown, sections):
        if len(t.header) == 2 and norm_header(t.header[0]) in FIELD_HEADERS:
            for row in t.rows:
                if norm_header(row.get(t.header[0], "")) in (
                    "document id",
                    "doc id",
                    "document number",
                ):
                    value = row.get(t.header[1], "").strip()
                    if value:
                        return value
    return None


def looks_like_person(text: str) -> bool:
    """True if the text looks like a person's name (not a team, group or placeholder)."""
    text = text.strip()
    return bool(NAME_RE.match(text)) and not NOT_PERSON.search(text)


class Extractor:
    """Rule-based extraction of pages and links from one converted document."""

    def __init__(self, vocab: Vocabulary):
        self.v = vocab

    # ---- slugs ---------------------------------------------------------------

    def doc_slug(self, key: str) -> str:
        """Slug of the source_document page for a document key."""
        return f"{DOC_DIR}/{key}"

    def entity_slug(self, vocab_type: str, key: str, item: str) -> str:
        """Slug for an entity: shared types by name, document types by document key and ID."""
        t = self.v.types[vocab_type]
        if t.scope == "global":
            return f"{t.dir}/{slugify(item)}"
        return f"{t.dir}/{key}/{slugify(item, 40)}"

    def safe_key(self, header: str, vocab_type: str) -> str:
        """Frontmatter key for a column, renamed when gbrain reserves it or reads it as a link."""
        key = norm_header(header).replace(" ", "_") or "value"
        if vocab_type == "person" and key == "title":
            return "job_title"
        if key in RESERVED_KEYS or key in self.v.verbs:
            return f"{key}_text"
        return key

    # ---- main entry ----------------------------------------------------------

    def extract(
        self, path: Path, conv: Converted, reference: str, kind: str, key: str, sha256: str
    ) -> DocumentResult:
        """Extract every page, link and shared entity from one converted document."""
        md = conv.markdown
        sections = split_sections(md, "Turns" if kind in ("transcript", "chat") else "Part")
        tables = parse_tables(md, sections)
        title = self._title(md, conv, path)
        doc_slug = self.doc_slug(key)
        res = DocumentResult(
            key=key,
            source_path=str(path),
            sha256=sha256,
            kind=kind,
            title=title,
            markdown=md,
            converter=conv.converter,
            sections=sections,
            tables=tables,
            pages=[],
            globals=[],
            warnings=list(conv.warnings),
            reference_text=reference,
            meta=conv.meta,
        )
        self._globals: dict[str, GlobalContribution] = {}
        self._res = res

        doc = Page(
            slug=doc_slug,
            type=self.v.gbrain_type("source_document"),
            title=title,
            fields={
                "doc_kind": kind,
                "doc_key": key,
                "source_file": path.name,
                "source_sha256": sha256,
                "converter": conv.converter,
                "char_count": len(md),
                "section_count": len(sections),
                "table_count": len(tables),
            },
        )
        for k in (
            "page_count",
            "row_count",
            "sheet_count",
            "slide_count",
            "subject",
            "date",
            "message_id",
        ):
            if conv.meta.get(k):
                doc.fields[k] = conv.meta[k]

        registry = self._register_ids(tables, key)
        self._document_fields(tables, doc, registry)
        self._entity_pages(tables, sections, md, key, kind, doc_slug, registry, title)
        self._approvals(tables, doc_slug, registry)
        self._participants(conv, kind, doc)
        section_pages = self._section_pages(sections, md, key, kind, doc_slug, title, registry)

        res.entity_ids = {i: r["slug"] for i, r in registry.items()}
        known_prefixes = set(self.v.prefix_map) | {i.split("-")[0] for i in registry}
        res.undefined_refs = sorted(
            {
                r
                for r in ID_REF_RE.findall(md)
                if r not in registry and r.rsplit("-", 1)[0] in known_prefixes
            }
        )

        for g in self._globals.values():
            g.add_link("appears_in", doc_slug)
        res.globals = list(self._globals.values())
        doc.body = self._doc_body(res, doc)
        res.pages = [doc, *section_pages, *res.pages]
        return res

    # ---- helpers -------------------------------------------------------------

    def _title(self, md: str, conv: Converted, path: Path) -> str:
        m = re.search(r"^# +(.+?)\s*#*\s*$", md, re.M)
        if m:
            return m.group(1).strip()
        if conv.meta.get("subject"):
            return conv.meta["subject"]
        return path.stem.replace("_", " ").replace("-", " ").strip().title()

    def _global(self, vocab_type: str, name: str) -> GlobalContribution:
        slug = self.entity_slug(vocab_type, "", name)
        if slug not in self._globals:
            self._globals[slug] = GlobalContribution(slug=slug, vocab_type=vocab_type, name=name)
        return self._globals[slug]

    def person(self, name: str) -> GlobalContribution:
        """The shared person contribution for a name (created on first use)."""
        return self._global("person", name)

    def _id_column(self, table: Table) -> str | None:
        for h in table.header:
            if norm_header(h) in ID_HEADERS:
                vals = [r[h] for r in table.rows if r.get(h)]
                if vals and all(ID_CELL_RE.match(v) for v in vals):
                    return h
        for h in table.header[:2]:
            vals = [r[h] for r in table.rows if r.get(h)]
            if len(vals) >= 1 and all(ID_CELL_RE.match(v) for v in vals):
                return h
        return None

    def _title_column(self, table: Table, id_col: str, vocab_type: str) -> str:
        candidates = [h for h in table.header if h != id_col]
        prefs = self.v.types[vocab_type].title_columns + ["name", "title", "description"]
        for p in prefs:
            for h in candidates:
                if norm_header(h) == p:
                    return h

        # Longest average text among columns without ID references.
        def avg(h):
            vals = [r.get(h, "") for r in table.rows]
            return sum(len(x) for x in vals) / max(len(vals), 1)

        plain = [
            h for h in candidates if not any(ID_REF_RE.search(r.get(h, "")) for r in table.rows)
        ]
        return max(plain or candidates, key=avg)

    def _register_ids(self, tables: list[Table], key: str) -> dict:
        """Pass 1: every ID defined in an ID-keyed table -> type, slug, row."""
        registry: dict[str, dict] = {}
        for t in tables:
            id_col = self._id_column(t)
            if not id_col:
                continue
            # A table whose IDs are all defined earlier only references them (e.g. approvals).
            ids = {r.get(id_col, "").strip() for r in t.rows} - {""}
            if ids and ids <= registry.keys():
                continue
            t.id_col = id_col
            section_type = self.v.type_for_heading(self._res.sections[t.section].heading)
            for row in t.rows:
                item_id = row.get(id_col, "").strip()
                if not item_id:
                    continue
                prefix = item_id.rsplit("-", 1)[0]
                vocab_type = self.v.type_for_prefix(prefix) or section_type or "record"
                if vocab_type in ("source_document", "document_section"):
                    vocab_type = "record"
                if item_id in registry:
                    self._res.warnings.append(
                        f"ID {item_id} is defined more than once; kept the first"
                    )
                    continue
                title_col = self._title_column(t, id_col, vocab_type)
                name = row.get(title_col, "").strip() or item_id
                slug = (
                    self.entity_slug(vocab_type, key, name)
                    if self.v.types[vocab_type].scope == "global"
                    else self.entity_slug(vocab_type, key, item_id)
                )
                registry[item_id] = {
                    "type": vocab_type,
                    "slug": slug,
                    "row": row,
                    "table": t,
                    "id_col": id_col,
                    "title_col": title_col,
                    "name": name,
                }
            t.mapped_to = "entities: " + ", ".join(
                sorted({r["type"] for r in registry.values() if r["table"] is t})
            )
        return registry

    def _refs(self, cell: str, registry: dict) -> list[str]:
        return [registry[r]["slug"] for r in ID_REF_RE.findall(cell) if r in registry]

    def _describe(self, cell: str, registry: dict) -> str:
        return ID_REF_RE.sub(
            lambda m: (
                f"{m.group()} ({registry[m.group()]['name']})"
                if m.group() in registry
                else m.group()
            ),
            cell,
        )

    def _people_in(self, cell: str) -> list[GlobalContribution]:
        parts = [
            p.strip()
            for p in re.split(r",|;|/|\band\b|&", re.sub(r"\(.*?\)", "", cell))
            if p.strip()
        ]
        return [self.person(p) for p in parts if looks_like_person(p)]

    def _link_value(self, verb: str, cell: str, registry: dict) -> list[str]:
        """Targets for a link-typed value: ID references, or names for person/org verbs."""
        refs = self._refs(cell, registry)
        if refs:
            return refs
        target = self.v.verbs[verb].target
        if target == "person":
            return [p.slug for p in self._people_in(cell)]
        if target == "organization" and cell.strip():
            return [self._global("organization", cell.strip()).slug]
        return []

    # ---- document control ------------------------------------------------------

    def _document_fields(self, tables: list[Table], doc: Page, registry: dict) -> None:
        for t in tables:
            if (
                len(t.header) != 2
                or norm_header(t.header[0]) not in FIELD_HEADERS
                or norm_header(t.header[1]) not in VALUE_HEADERS
                or t.id_col
            ):
                continue
            fcol, vcol = t.header
            for row in t.rows:
                field, value = row.get(fcol, "").strip(), row.get(vcol, "").strip()
                if not field or not value:
                    continue
                mapped = self.v.document_fields.get(norm_header(field))
                if mapped and mapped in self.v.verbs:
                    targets = self._link_value(mapped, value, registry)
                    for target in targets:
                        doc.add_link(mapped, target)
                    if not targets:
                        doc.fields[self.safe_key(field, "source_document")] = value
                else:
                    doc.fields[mapped or self.safe_key(field, "source_document")] = value
            t.mapped_to = "document attributes"

    # ---- entity pages -----------------------------------------------------------

    def _entity_pages(
        self, tables, sections, md, key, kind, doc_slug, registry, doc_title
    ) -> None:
        for item_id, r in registry.items():
            vocab_type, row, t = r["type"], r["row"], r["table"]
            section = sections[t.section]
            attrs, links, lines = {}, {}, []
            for col, cell in row.items():
                if col in (r["id_col"], r["title_col"]) or not cell.strip():
                    continue
                verb = self.v.verb_for_column(col)
                targets = (
                    self._link_value(verb, cell, registry) if verb else self._refs(cell, registry)
                )
                if targets:
                    for target in targets:
                        if target != r["slug"]:
                            links.setdefault(verb or "references", []).append(target)
                    lines.append(f"- **{col}:** {self._describe(cell, registry)}")
                else:
                    attrs[self.safe_key(col, vocab_type)] = cell
                    lines.append(f"- **{col}:** {cell}")

            context = f"From {doc_title} ({key}), section {section.index + 1}: {section.heading}."
            if self.v.types[vocab_type].scope == "global":
                g = self._global(vocab_type, r["name"])
                g.attributes.update(attrs)
                g.attributes.setdefault("item_id", item_id)
                for verb, targets in links.items():
                    for target in targets:
                        g.add_link(verb, target)
                g.notes += [
                    f"{item_id} in {doc_title} ({key}), "
                    f"section {section.index + 1}: {section.heading}.",
                    *lines,
                ]
                continue
            page = Page(
                slug=r["slug"],
                type=self.v.gbrain_type(vocab_type),
                title=f"{item_id}: {r['name']}",
                fields={
                    "item_id": item_id,
                    "doc_key": key,
                    "doc_kind": kind,
                    "section": f"{section.index + 1}. {section.heading}",
                    **attrs,
                },
            )
            page.add_link("defined_in", doc_slug)
            for verb, targets in links.items():
                for target in targets:
                    page.add_link(verb, target)
            page.body = "\n".join([f"# {item_id}: {r['name']}", "", context, "", *lines]) + "\n"
            self._res.pages.append(page)

    def _approvals(self, tables, doc_slug, registry) -> None:
        for t in tables:
            if t.id_col:
                continue
            headers = {norm_header(h): h for h in t.header}
            decision = next((h for n, h in headers.items() if n in APPROVAL_HEADERS), None)
            approver = next((h for n, h in headers.items() if n in APPROVER_HEADERS), None)
            if not decision or not approver or t.mapped_to != "text only":
                continue
            for row in t.rows:
                cell = row.get(approver, "")
                people = [
                    self._global_for_slug(s) for s in self._refs(cell, registry)
                ] or self._people_in(cell)
                for p in people:
                    if p is None:
                        continue
                    p.add_link("approved", doc_slug)
                    details = {
                        self.safe_key(f"approval {h}", "person"): v
                        for h, v in row.items()
                        if h != approver and v.strip()
                    }
                    p.attributes.update(details)
                    p.notes.append(
                        "Approval of "
                        + self._res.title
                        + ": "
                        + "; ".join(
                            f"{h}: {v}" for h, v in row.items() if h != approver and v.strip()
                        )
                    )
            t.mapped_to = "approvals"

    def _global_for_slug(self, slug: str) -> GlobalContribution | None:
        return self._globals.get(slug)

    # ---- email / transcript / chat ------------------------------------------------

    def _participants(self, conv: Converted, kind: str, doc: Page) -> None:
        meta = conv.meta
        if kind == "email":
            for field, verb in (("from", "sent_by"), ("to", "sent_to"), ("cc", "copied_to")):
                for name, addr in meta.get(field, []):
                    if not (name or addr):
                        continue
                    p = self.person(person_name_from_address(name, addr))
                    if addr:
                        emails = p.attributes.setdefault("email", [])
                        if addr.lower() not in emails:
                            emails.append(addr.lower())
                    doc.add_link(verb, p.slug)
                    p.notes.append(
                        f'{verb.replace("_", " ").capitalize()} the email "{self._res.title}".'
                    )
        elif kind in ("transcript", "chat"):
            for name in meta.get("participants", []):
                if looks_like_person(name) or len(name.split()) == 1:
                    p = self.person(name)
                    doc.add_link("has_participant", p.slug)
                    p.notes.append(f'Participant in the {kind} "{self._res.title}".')

    # ---- sections -------------------------------------------------------------

    def _section_pages(self, sections, md, key, kind, doc_slug, doc_title, registry) -> list[Page]:
        pages = []
        width = len(str(len(sections)))
        for s in sections:
            text = s.text(md)
            page = Page(
                slug=f"{SECTION_DIR}/{key}/{s.index + 1:0{width}d}-{slugify(s.heading, 40)}",
                type=self.v.gbrain_type("document_section"),
                title=f"{doc_title} — {s.heading}",
                fields={
                    "doc_key": key,
                    "doc_kind": kind,
                    "section_index": s.index + 1,
                    "heading": s.heading,
                    "heading_level": s.level,
                    "char_start": s.start,
                    "char_end": s.end,
                },
                body=text,
            )
            page.add_link("section_of", doc_slug)
            for ref in ID_REF_RE.findall(text):
                if ref in registry:
                    page.add_link("mentions_item", registry[ref]["slug"])
            pages.append(page)
        return pages

    def _doc_body(self, res: DocumentResult, doc: Page) -> str:
        counts: dict[str, int] = {}
        for p in res.pages:
            counts[p.type] = counts.get(p.type, 0) + 1
        for g in res.globals:
            t = self.v.gbrain_type(g.vocab_type)
            counts[t] = counts.get(t, 0) + 1
        lines = [
            f"# {res.title}",
            "",
            f"Source document `{Path(res.source_path).name}` ({res.kind}), "
            f"converted with {res.converter}.",
            "",
            "## Attributes",
            "",
        ]
        lines += [f"- **{k}:** {v}" for k, v in doc.fields.items()]
        lines += ["", "## Sections", ""]
        lines += [f"{s.index + 1}. {s.heading}" for s in res.sections]
        if counts:
            lines += ["", "## Extracted items", ""]
            lines += [f"- {t}: {n}" for t, n in sorted(counts.items())]
        return "\n".join(lines) + "\n"
