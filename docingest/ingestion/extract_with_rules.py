# What this file does: the rule-based extraction. It reads one document's markdown and makes its
# pages without any AI: the document's own page, one page per section, entity pages for ID rows,
# list items and named things, shared people and organizations, and the links between them. It
# needs no API key and always runs. The steps run in the order set in Extractor.extract().

"""Rule-based extraction: document, section, and entity pages from markdown.

Always runs, needs no API key. Produces:
  - one source_document page per input file
  - one document_section page per section; together they hold the full text
  - entity pages for rows of ID-keyed tables (FR-001, RK-02, ...)
  - entity pages for list items under a heading that names one type, or that start
    with an ID ("- FR-1.1 The system shall ...")
  - shared person / organization / system / glossary entities
  - links from table columns, IDs in table cells and list items, system names in
    text, document-control fields, approvals, email headers, and transcript speakers
"""

from __future__ import annotations

import re
from pathlib import Path

from docingest.ingestion.normalize_headings import TITLE_PHRASES
from docingest.utils.data_types import (
    Converted,
    DocumentResult,
    GlobalContribution,
    ListBlock,
    Page,
    Table,
)
from docingest.utils.markdown_parsing import (
    normalize_ws,
    parse_lists,
    parse_tables,
    slugify,
    split_sections,
)
from docingest.vocabulary.vocabulary_loader import Vocabulary, header_key, norm_header

ID_PATTERN = r"[A-Z][A-Z0-9]{0,7}-\d{1,5}(?:\.\d{1,3})*[A-Z]?"  # BR-01, FR-1.1, US-12A
# A table cell that is exactly one ID, such as FR-001.
ID_CELL_RE = re.compile(rf"^{ID_PATTERN}$")
# An ID found anywhere inside a longer piece of text.
ID_REF_RE = re.compile(rf"\b{ID_PATTERN}\b")
# A list item that starts with its own ID: "FR-1.1 The ...", "**FR-1** ...", "NFR-03: ...".
LEADING_ID_RE = re.compile(rf"^(?P<id>{ID_PATTERN})\b[\s:.\-–—]*(?P<rest>.*)$")
# ID references written as a trace: "[BR-01]", "(US-1)", "(BR-02, BR-03)".
TRACE_RE = re.compile(rf"\s*[\[(]\s*{ID_PATTERN}(?:\s*[,;]\s*{ID_PATTERN})*\s*[\])]")
# "Integrations: core banking, credit bureau" -> a label that may name a type, then names.
LABEL_RE = re.compile(r"^(?P<label>[A-Za-z][\w /&-]{1,40}):\s+(?P<rest>.+)$")
# Bold, italic and code marks. They are removed before text is compared or stored.
MARKUP_RE = re.compile(r"(\*\*|__|`)")
# Words that show a list is out of scope (exclusions).
SCOPE_OUT_RE = re.compile(
    r"\b(out of scope|not in scope|not include|not included|does not|do not|excluded"
    r"|exclusions?|non goals?)\b"
)
# Words that show a list is in scope.
SCOPE_IN_RE = re.compile(r"\b(in scope|includes?|included)\b")
# Types a plain list item (no ID) may become: statements made by the document. People,
# organizations and systems need a name, not a sentence, so they come only from a label.
LIST_PRIMITIVES = {"concept", "annotation", "temporal"}
NAMED_LIST_PRIMITIVES = {"entity"}  # types a "Label: a, b, c" item may list by name
# The longest name a 'Label: a, b' item may list. Longer text is a sentence, not a list of names.
MAX_NAME_WORDS = 6
# Acceptance criteria written as behaviour: "Given ..., when ..., then ...".
GIVEN_WHEN_THEN_RE = re.compile(r"^given\b.*\bwhen\b.*\bthen\b", re.I)
# "As a <role>, I want ..." (user stories); the role is matched to a persona by name.
STORY_ACTOR_RE = re.compile(r"\bas (?:an?|the)\s+(?P<who>[^,.:;]{2,60}?),\s*i\b", re.I)
# A lead-in line longer than this is a sentence, not a label, so it is not used.
LEAD_IN_MAX_WORDS = 8
# A cover line: "Label: value", also "**Label:** value" and "- **Label:** value".
COVER_LINE_RE = re.compile(r"^(?:[-*•]\s+)?(?P<label>[A-Za-z][A-Za-z0-9 ./&()'-]{1,40}?):\s+(?P<value>\S.*)$")
COVER_SECTIONS = 2  # cover lines are read in the first sections, before any numbered one
# A label that names a role: an unmapped cover line with such a label and a person's name
# as its value gives that person ("Design lead: Hana Sato").
PERSON_LABEL_RE = re.compile(
    r"\b(lead|manager|owner|author|reviewer|approver|sponsor|designer|engineer|analyst|contact"
    r"|editor|architect|director|officer|coordinator|prepared|reviewed|approved)\b",
    re.I,
)
# "For the Department of Revenue" / "On behalf of Acme Ltd": starts a signature block.
SIGN_FOR_RE = re.compile(
    r"^(?:#+\s*)?(?:\*\*|__)?(?:for|on behalf of)\s+(?:the\s+)?(?P<org>[^:*]+?)(?:\*\*|__)?:?\s*$",
    re.I,
)
# Column headers that name the ID column of a table.
ID_HEADERS = {"id", "ref", "reference", "ref no", "key", "no", "item id", "req id", "number"}
# Column headers of a two-column field table (Field | Value).
FIELD_HEADERS = {"field", "property", "attribute", "item", "key", "parameter"}
# Second-column headers of a field table.
VALUE_HEADERS = {"value", "details", "description", "entry"}
# Column headers that hold a decision (approved, rejected, ...) in a sign-off table.
APPROVAL_HEADERS = {
    "decision",
    "approval",
    "approval status",
    "approved",
    "sign off",
    "signoff",
    "signature",
}
# Column headers that name the person approving.
APPROVER_HEADERS = {
    "stakeholder",
    "approver",
    "name",
    "signatory",
    "approved by",
    "reviewer",
    "name role",
    "name and role",
}
# Words that mean approved.
APPROVED_RE = re.compile(r"\b(approved|signed|accepted|agreed|yes)\b", re.I)
# Words that mean not approved. They override the approved words.
NOT_APPROVED_RE = re.compile(r"\b(pending|not|rejected|declined|awaiting|no)\b", re.I)
# An approver cell in the form 'Name — Role'.
NAME_ROLE_RE = re.compile(r"^(?P<name>[^—–]+?)\s+[—–-]\s+(?P<role>.+)$")
# Column headers for a document version.
VERSION_HEADERS = {"version", "revision", "rev", "ver"}
# Column headers for the author of a change.
AUTHOR_HEADERS = {"author", "authors", "changed by", "by", "editor"}
# Column headers that describe what changed.
CHANGE_HEADERS = {
    "changes", "change", "what changed", "change summary", "summary of changes",
    "description of change", "description of changes",
}  # fmt: skip
# Column headers for a date.
DATE_HEADERS = {"date", "date signed", "signed on", "signature date", "approval date"}
# Column headers that mean a signature (signed or not).
SIGNATURE_HEADERS = {"signature", "signed", "sign off", "signoff"}
# A heading that names signatures or sign-offs.
SIGNATURE_HEADING_RE = re.compile(r"\b(signatures?|sign[- ]?offs?|signatories)\b", re.I)
# Columns that name people; the column is a people column only when most values are names.
PERSON_HEADERS = {"name", "full name", "person", "contact", "contact name", "member", "signatory"}
# Row labels of a two-column cover table ("Document ID | BRD-7") besides the document fields.
FIELD_LABELS = {
    "document id", "doc id", "document number", "version", "date", "status", "title",
    "effective date", "last updated", "reference",
}  # fmt: skip
# First-column headers of a table whose rows are grouped by type (Category | Detail).
CATEGORY_HEADERS = {"category", "type", "kind"}
# Columns that say only "these are related": a generic `references` link is the right reading.
GENERIC_LINK_COLUMNS = {
    "related", "related to", "related items", "relates to", "references", "reference", "refs",
    "see also", "linked items", "links", "trace", "traces", "traceability", "cross reference",
}  # fmt: skip
# Column names too general to say which type a table holds.
GENERIC_TITLE_COLUMNS = {
    "name", "title", "description", "requirement", "item", "category", "details", "detail",
}  # fmt: skip
# The word 'sponsor' in a sign-off row marks that person as the sponsor.
SPONSOR_RE = re.compile(r"\bsponsor\b", re.I)
# Looks like a name: one to four capitalised words.
NAME_RE = re.compile(r"^[A-Z][a-zA-Z.'\-]+(?: [A-Z][a-zA-Z.'\-]+){0,3}$")
# Words that rule out a person's name: teams, groups, placeholders and job titles.
NOT_PERSON = re.compile(
    r"\b(team|department|group|office|committee|board|all|tbd|n/?a|none"
    # job titles: "Priya Natarajan, Business Analyst" names one person, not two
    r"|analyst|manager|officer|director|lead|engineer|head|chief|coordinator|specialist"
    r"|consultant|architect|developer|tester|administrator|supervisor|president)\b",
    re.I,
)
# Attribute keys gbrain reads as links or reserves; renamed to avoid accidental edges. Attribute
# names that gbrain reads as its own fields or links. A column with one of these names is renamed
# (see safe_key).
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

# Folder of the document pages (docs/documents/<key>).
DOC_DIR = "docs/documents"
# Folder of the section pages.
SECTION_DIR = "docs/sections"


# A display name for an email sender. The name in the header is used when there is one. Otherwise a
# name is built from the address (jane.doe@... becomes Jane Doe).
def person_name_from_address(name: str, addr: str) -> str:
    """A person's display name from an email header, or one derived from the address."""
    if name and "@" not in name:
        return normalize_ws(name.strip("'\" "))
    local = (addr or name).split("@")[0]
    return " ".join(p.capitalize() for p in re.split(r"[._\-+]+", local) if p) or addr


# The document's own ID, from a 'Document ID' row in a field table, such as BRD-7. Used to name the
# document.
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


# Decides whether a cell or a line is a person before a person page is made.
def looks_like_person(text: str) -> bool:
    """True if the text looks like a person's name (not a team, group or placeholder)."""
    text = text.strip()
    return bool(NAME_RE.match(text)) and not NOT_PERSON.search(text)


# Reads one converted document and builds its pages. Its work runs in one fixed order, set out in
# extract().
class Extractor:
    """Rule-based extraction of pages and links from one converted document."""

    # Keep the vocabulary, which says which types, verbs and columns exist.
    def __init__(self, vocab: Vocabulary):
        self.v = vocab

    # ---- slugs ---------------------------------------------------------------

    # Page address of the document itself, such as docs/documents/brd-lh-001.
    def doc_slug(self, key: str) -> str:
        """Slug of the source_document page for a document key."""
        return f"{DOC_DIR}/{key}"

    # Page address for an entity. Shared types (people, organizations, systems, terms) are named by
    # their name, so every document uses the same page. Document-specific types also carry the
    # document key, so their IDs stay unique.
    def entity_slug(self, vocab_type: str, key: str, item: str) -> str:
        """Slug for an entity: shared types by name, document types by document key and ID."""
        t = self.v.types[vocab_type]
        if t.scope == "global":
            return f"{t.dir}/{slugify(item)}"
        return f"{t.dir}/{key}/{slugify(item, 40)}"

    # Turn a column name into an attribute name. A person's 'Title' becomes job_title, and names
    # that gbrain reserves get a _text suffix.
    def safe_key(self, header: str, vocab_type: str) -> str:
        """Frontmatter key for a column, renamed when gbrain reserves it or reads it as a link."""
        key = header_key(header).replace(" ", "_") or "value"
        if vocab_type == "person" and key == "title":
            return "job_title"
        if key in RESERVED_KEYS or key in self.v.verbs:
            return f"{key}_text"
        return key

    # ---- main entry ----------------------------------------------------------

    # The main entry point. Steps, in order: IDs in tables, document fields and cover lines,
    # version history, tables without IDs, headings and list items that are items, user story
    # actors, entity pages, party roles, approvals, signature blocks, participants, then section
    # pages. Returns the whole DocumentResult.
    def extract(
        self, path: Path, conv: Converted, reference: str, kind: str, key: str, sha256: str
    ) -> DocumentResult:
        """Extract every page, link and shared entity from one converted document."""
        md = conv.markdown
        sections = split_sections(md, "Turns" if kind in ("transcript", "chat") else "Part")
        tables = parse_tables(md, sections)
        lists = parse_lists(md, sections)
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
        # Shared entities collected while the document is read. They are merged into the whole
        # brain later.
        self._globals: dict[str, GlobalContribution] = {}
        self._res = res

        # The document's own page. It holds the facts about the file: its kind, key, hash and size.
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
        # Copy any extra facts the converter recorded (page count, sheet count, subject and so on)
        # onto the page.
        for k in (
            "page_count",
            "row_count",
            "sheet_count",
            "slide_count",
            "subject",
            "date",
            "message_id",
            "headings_normalized",
        ):
            if conv.meta.get(k):
                doc.fields[k] = conv.meta[k]

        # Pass 1: find every ID defined in a table. Everything else refers back to this registry.
        registry = self._register_ids(tables, key)
        self._registry = registry
        self._document_fields(tables, doc, registry)
        self._cover_fields(sections, md, doc, registry)
        self._version_history(tables, doc)
        self._register_keyless(tables, key, registry)
        self._register_by_subject(tables, key, registry)
        self._register_heading_items(sections, key, registry)
        self._register_list_items(lists, key, registry)
        self._link_story_actors(sections, md, registry)
        self._entity_pages(tables, sections, md, key, kind, doc_slug, registry, title)
        self._party_roles(doc, registry)
        self._approvals(tables, doc, registry)
        self._signature_blocks(sections, md, doc)
        self._participants(conv, kind, doc)
        section_pages = self._section_pages(sections, md, key, kind, doc_slug, title, registry)

        # Map each item ID to its page address, so verification can check that every row became an
        # entity.
        res.entity_ids = {i: r["slug"] for i, r in registry.items()}
        # ID prefixes the vocabulary knows, plus any used in this document. A reference with one of
        # these prefixes that is not defined here is a dangling reference.
        known_prefixes = set(self.v.prefix_map) | {i.split("-")[0] for i in registry}
        # IDs that are referenced but not defined. They are reported, never invented.
        res.undefined_refs = sorted(
            {
                r
                for r in ID_REF_RE.findall(md)
                if r not in registry and r.rsplit("-", 1)[0] in known_prefixes
            }
        )

        # Every shared entity points back to this document.
        for g in self._globals.values():
            g.add_link("appears_in", doc_slug)
        res.globals = list(self._globals.values())
        # The document page's text: its title, attributes, sections and counts of what was
        # extracted.
        doc.body = self._doc_body(res, doc)
        res.pages = [doc, *section_pages, *res.pages]
        return res

    # ---- helpers -------------------------------------------------------------

    # True when the text contains a document-title phrase such as 'statement of work', as whole
    # words.
    @staticmethod
    def _is_title_phrase(text: str) -> bool:
        """True when the text contains a document-title phrase ("statement of work")."""
        t = f" {norm_header(text)} "
        return any(f" {p} " in t for p in TITLE_PHRASES)

    # Find a title written as plain text before the first heading. Word often stores titles that
    # way.
    def _preamble_title(self, md: str, first_h1: re.Match | None) -> str | None:
        """A title written as plain lines before the first heading (Word's Title style).

        Used when the line holds a document-title phrase, or when the first heading is just a
        numbered section ("1. Document Control"). A following short plain line names the
        subject unless the title already does ("Business Requirements Document: Payments").
        """
        end = first_h1.start() if first_h1 else len(md)
        lines = [ln.strip() for ln in md[:end].splitlines() if ln.strip()]
        # The first line must be short and plain: not a heading, table, quote or list item.
        if not lines or len(lines[0]) > 120 or re.match(r"^[#|>\-*]", lines[0]):
            return None
        # A first heading that starts with a number ('1. Document Control') is a section, so the
        # plain line above it is the title.
        numbered = bool(first_h1 and re.match(r"\d", first_h1.group(1).strip()))
        if not (self._is_title_phrase(lines[0]) or numbered):
            return None
        title = MARKUP_RE.sub("", lines[0]).strip()
        nxt = lines[1] if len(lines) > 1 else ""
        # A short plain line under the title names the subject, unless the title already has a
        # subject.
        if ":" not in title and nxt and len(nxt) <= 80 and not re.search(r"[:|·]", nxt):
            title = f"{title}: {nxt}"
        return title

    # The document's title, in order of preference: a plain title before the first heading, the
    # first # heading (with its subject line), the email subject, and finally the file name.
    def _title(self, md: str, conv: Converted, path: Path) -> str:
        # The first top-level heading.
        m = re.search(r"^# +(.+?)\s*#*\s*$", md, re.M)
        pre = self._preamble_title(md, m)
        if pre:
            return pre
        if m:
            title = m.group(1).strip()
            # A title phrase followed directly by a deeper, unnumbered heading names the subject
            # ("# STATEMENT OF WORK" / "## Website Accessibility Audit").
            next_line = md[m.end() :].lstrip("\n").split("\n", 1)[0]
            sub = re.match(r"#{2,6}[ \t]+(?P<t>\S.*?)\s*#*\s*$", next_line)
            if sub and self._is_title_phrase(title) and not re.match(r"\d", sub.group("t")):
                return f"{title}: {sub.group('t').strip()}"
            # A short plain line right under a generic title names the subject
            # ("Business Requirements Document" / "Digital Loan Origination System").
            after = [ln.strip() for ln in md[m.end() :].splitlines()[:4] if ln.strip()]
            nxt = after[0] if after else ""
            if nxt and len(nxt) <= 80 and not re.match(r"^[#|*_>\-\d]", nxt) and ":" not in nxt:
                return f"{title}: {nxt}"
            return title
        # An email takes its subject line as its title.
        if conv.meta.get("subject"):
            return conv.meta["subject"]
        return path.stem.replace("_", " ").replace("-", " ").strip().title()

    # Reuse an organization already known under a name that overlaps this one, so 'Calder Freight'
    # and 'Calder Freight Ltd' become one page.
    def _same_organization(self, name: str) -> str:
        """An organization already known under a name that contains this one, or is contained
        in it as whole words ("Calder Freight" / "Calder Freight Ltd"); two words at least."""
        mine = f" {norm_header(name)} "
        for g in self._globals.values():
            if g.vocab_type != "organization":
                continue
            other = f" {norm_header(g.name)} "
            # Compare the two names as whole words. The shorter one must be at least two words to
            # count.
            short, long_ = sorted((mine, other), key=len)
            if len(short.split()) >= 2 and short in long_:
                return g.name
        return name

    # The shared contribution for a name, created on first use. Organization names are matched to a
    # known name first.
    def _global(self, vocab_type: str, name: str) -> GlobalContribution:
        if vocab_type == "organization":
            name = self._same_organization(name)
        slug = self.entity_slug(vocab_type, "", name)
        if slug not in self._globals:
            self._globals[slug] = GlobalContribution(slug=slug, vocab_type=vocab_type, name=name)
        return self._globals[slug]

    # Shorthand for a shared person.
    def person(self, name: str) -> GlobalContribution:
        """The shared person contribution for a name (created on first use)."""
        return self._global("person", name)

    # Find the column that holds the IDs. A header that names an ID wins. Otherwise the first two
    # columns are checked for ID-shaped values.
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

    # Pick the column that holds each row's name: the vocabulary's title columns first, then the
    # column with the most text that does not refer to other IDs.
    def _title_column(self, table: Table, id_col: str, vocab_type: str) -> str:
        candidates = [h for h in table.header if h != id_col]
        # This type's own title columns first, then the generic names.
        prefs = self.v.types[vocab_type].title_columns + ["name", "title", "description"]
        for p in prefs:
            for h in candidates:
                if norm_header(h) == p or header_key(h) == p:
                    return h

        # Longest average text among columns without ID references.
        # Average length of a column's text.
        def avg(h):
            vals = [r.get(h, "") for r in table.rows]
            return sum(len(x) for x in vals) / max(len(vals), 1)

        # Columns whose cells do not refer to other IDs. Only those can hold a name.
        plain = [
            h for h in candidates if not any(ID_REF_RE.search(r.get(h, "")) for r in table.rows)
        ]
        return max(plain or candidates, key=avg)

    # Pass 1: read every table that has an ID column, and register each row's ID with its type and
    # page address.
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
            # Remember which column holds the IDs. Verification reads it back.
            t.id_col = id_col
            # The section heading can name the type too (for example 'Risks'). It is used when the
            # ID prefix does not.
            section_type = self.v.type_for_heading(self._res.sections[t.section].heading)
            for row in t.rows:
                item_id = row.get(id_col, "").strip()
                if not item_id:
                    continue
                prefix = item_id.rsplit("-", 1)[0]
                # The row's type, from the most specific clue: its ID prefix, then its columns,
                # then its section heading. Failing all of these, the row becomes a generic record.
                vocab_type = (
                    self.v.type_for_prefix(prefix)
                    or self._type_from_columns(t, id_col)
                    or section_type
                    or "record"
                )
                # The document's own structure types are never used for a row. Such a row becomes a
                # generic record.
                if vocab_type in ("source_document", "document_section"):
                    vocab_type = "record"
                # An ID defined twice keeps its first definition. The second is reported as a
                # warning.
                if item_id in registry:
                    self._res.warnings.append(
                        f"ID {item_id} is defined more than once; kept the first"
                    )
                    continue
                # The column that holds the name of each row.
                title_col = self._title_column(t, id_col, vocab_type)
                # The name is the title cell, or the ID when the row has no title.
                name = row.get(title_col, "").strip() or item_id
                # Page address: shared types by name, the others by document key and ID.
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
                # A row whose prefix no type has is recorded as a schema gap, so a reviewer can
                # decide what it is.
                if vocab_type == "record" and not self.v.type_for_prefix(prefix):
                    self._prefix_gap(prefix, t, item_id, registry[item_id])
            t.mapped_to = "entities: " + ", ".join(
                sorted({r["type"] for r in registry.values() if r["table"] is t})
            )
        return registry

    # ---- schema gaps ---------------------------------------------------------------

    # Find or create a schema gap. A gap collects every row the vocabulary did not cover.
    def _gap(self, key: str, **fields) -> dict:
        """The gap recorded under ``key`` in this document (created on first use)."""
        for g in self._res.gaps:
            if g["key"] == key and g["section"] == fields["section"]:
                return g
        gap = {"key": key, **fields, "rows": []}
        self._res.gaps.append(gap)
        return gap

    # Record a row whose ID prefix no type has, as a gap for a reviewer to decide.
    def _prefix_gap(self, prefix: str, t: Table, item_id: str, r: dict) -> None:
        """A row typed ``record`` because no type has its ID prefix."""
        heading = self._res.sections[t.section].heading
        gap = self._gap(
            f"prefix:{prefix}",
            kind="unknown_prefix",
            prefix=prefix,
            section=t.section,
            heading=heading,
            header=list(t.header),
            title_column=r["title_col"],
        )
        gap["rows"].append(
            {"id": item_id, "name": r["name"], "slug": r["slug"], "cells": dict(r["row"])}
        )

    # Record a link that fell back to a generic verb as a gap, so a reviewer can map the column.
    def _column_gap(self, col: str, column_verb, verb: str, r: dict, item_id, src, target, cell):
        """A link from a table column that fell back to ``references``.

        No verb for the column: a gap a reviewer can map. A column verb whose types do
        not fit this row: reported only (the vocabulary maps a column to one verb).
        """
        # Only a fallback to 'references' is a gap. Record rows are covered by their prefix gap,
        # and generic columns such as 'Related' need no verb.
        if verb != "references" or src == "record" or header_key(col) in GENERIC_LINK_COLUMNS:
            return  # record rows are covered by their prefix gap; generic columns need no verb
        dst = self._slug_type(target)
        t = r["table"]
        # The column has a verb, but it does not fit these types: a mismatch. Otherwise the column
        # has no verb.
        if column_verb:
            key, kind = f"mismatch:{column_verb}:{src}->{dst}", "verb_mismatch"
        else:
            key, kind = f"column:{norm_header(col)}", "unmapped_column"
        gap = self._gap(
            key,
            kind=kind,
            column=col,
            verb=column_verb,
            section=t.section if t else r["section"],
            heading=self._res.sections[t.section if t else r["section"]].heading,
        )
        row = next((x for x in gap["rows"] if x["id"] == item_id), None)
        if row is None:
            row = {"id": item_id, "name": r["name"], "slug": r["slug"], "type": src,
                   "cell": cell, "targets": []}  # fmt: skip
            gap["rows"].append(row)
        # Each target is recorded once per row, with the type it resolved to.
        if [target, dst] not in row["targets"]:
            row["targets"].append([target, dst])

    # When the ID prefix is unknown, the table's first specific column can name the type ('Goal',
    # 'Milestone').
    def _type_from_columns(self, table: Table, id_col: str | None) -> str | None:
        """The type a table's first specific column names ("Goal" -> business_objective).

        When several types accept the column as a title, the type for which it is the
        main title column wins ("Milestone" -> milestone, not payment_term).
        """
        skip = ("source_document", "document_section")
        for h in table.header:
            key = header_key(h)
            if h == id_col or key in GENERIC_TITLE_COLUMNS:
                continue
            # A column can be a title column of several types. The type where it is the main title
            # column wins.
            ranked = sorted(
                (t.title_columns.index(key), t.name)
                for t in self.v.types.values()
                if key in t.title_columns and t.name not in skip
            )
            if ranked:
                return ranked[0][1]
        return None

    # Generate an ID for a row that has none: the type's prefix plus the next free number.
    def _new_id(self, vocab_type: str, registry: dict, preferred: str = "") -> str:
        """A generated ID for a row without one: STEP-4, KPI-2, AS-1, ..."""
        t = self.v.types[vocab_type]
        prefix = t.id_prefixes[0] if t.id_prefixes else vocab_type.upper()
        # A row whose own number (such as 4) is still free keeps it, so generated IDs match the
        # document's numbering.
        if preferred.isdigit() and f"{prefix}-{preferred}" not in registry:
            return f"{prefix}-{preferred}"
        n = 1
        while f"{prefix}-{n}" in registry:
            n += 1
        return f"{prefix}-{n}"

    # For a Category | Detail table: map each row to the type its category names. Returns None
    # unless every category is a known type.
    def _row_type_by_category(self, table: Table) -> dict[int, str] | None:
        """Rows of a "Category | Detail" table whose category names a type (Assumption, ...)."""
        first = table.header[0]
        if header_key(first) not in CATEGORY_HEADERS or len(table.header) < 2:
            return None
        out = {}
        for i, row in enumerate(table.rows):
            value = norm_header(row.get(first, "")).replace(" ", "_")
            # One unknown category means the table is not grouped by type at all.
            if value and value not in self.v.types:
                return None
            if value:
                out[i] = value
        return out or None

    # Pass 2: tables with no ID column, typed by their first column (Step, Metric, Term, Category).
    # Each row gets a generated ID.
    def _register_keyless(self, tables: list[Table], key: str, registry: dict) -> None:
        """Pass 2: tables without an ID column whose first column names a type.

        "Step | Stage | ..." rows become process steps, "Metric | Baseline | Target"
        rows KPIs, "Term | Definition" rows glossary terms, "Stakeholder | ..." rows
        stakeholder groups, and "Category | Detail" rows the type their category names.
        Each row gets a generated ID (STEP-4, KPI-2, AS-1).
        """
        for t in tables:
            if t.id_col or t.mapped_to != "text only" or not t.header or not t.rows:
                continue
            # Sign-off tables are read by _approvals, not here.
            if self._approval_columns(t):
                continue  # sign-off tables are read by _approvals
            by_category = self._row_type_by_category(t)
            # The type named by this table's first column, if the vocabulary lists it as a keyless
            # column.
            table_type = self.v.keyless_map.get(header_key(t.header[0]))
            if not by_category and not table_type:
                continue
            first = t.header[0]
            previous = None  # rows of a process table follow each other in order
            for i, row in enumerate(t.rows):
                vocab_type = by_category.get(i) if by_category else table_type
                if not vocab_type:
                    continue
                # In a category table, the name is the longest other cell in the row.
                if by_category:
                    title_col = max(t.header[1:], key=lambda h: len(row.get(h, "")))
                else:
                    title_col = self._title_column(t, None, vocab_type)
                name = row.get(title_col, "").strip()
                if not name:
                    continue
                # Use the row's own number (1, 2, ...) when it has one, so the ID matches the
                # document.
                item_id = self._new_id(vocab_type, registry, row.get(first, "").strip())
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
                    "id_col": first if by_category else None,
                    "title_col": title_col,
                    "name": name,
                    "generated": True,
                    "follows": previous if vocab_type == "process_step" else None,
                }
                previous = item_id
            types = sorted({r["type"] for r in registry.values() if r["table"] is t})
            if types:
                t.mapped_to = "entities: " + ", ".join(types)

    # The type a column header names, such as 'Connected system' becoming system. Returns None when
    # the header names no type or names several.
    def _type_of_header(self, header: str) -> str | None:
        """The type a column header names, by the types' title columns.

        The whole header first, else its last word, the noun ("Connected system" ->
        system, "Acceptance criterion" -> acceptance_criterion). General words (name,
        description, item, ...) name no type. When several types accept the word, the
        type for which it is the main title column wins.
        """
        key = header_key(header)
        # A header that names two different types ('Question or risk') is ambiguous, so it names
        # neither.
        if len({self._type_of_word(w) for w in key.split()} - {None}) > 1:
            return None  # "Question or risk" names two types, so neither
        return self._type_of_word(key) or self._type_of_word(key.rsplit(" ", 1)[-1])

    # The type whose title columns include this word. A tie goes to the type where the word is the
    # main title column.
    def _type_of_word(self, word: str) -> str | None:
        """The type whose title columns include this word (its main title column first)."""
        if not word or word in GENERIC_TITLE_COLUMNS:
            return None
        # Several types may name this word. The one where it is the main title column (index 0)
        # wins.
        ranked = sorted(
            (t.title_columns.index(word), t.name)
            for t in self.v.types.values()
            if word in t.title_columns and t.name not in ("source_document", "record")
        )
        return ranked[0][1] if ranked else None

    # A Name-type column whose values are mostly people's names. Used to recognise people tables.
    def _people_column(self, table: Table) -> str | None:
        """A Name-type column whose values are mostly people's names."""
        for h in table.header:
            if header_key(h) not in PERSON_HEADERS:
                continue
            vals = [r.get(h, "").strip() for r in table.rows if r.get(h, "").strip()]
            # At least half of the values must look like people's names.
            if vals and sum(looks_like_person(v) for v in vals) * 2 > len(vals):
                return h
        return None

    # The column that says what each row is about. When the first column only numbers the rows, the
    # second is used.
    def _subject_column(self, table: Table) -> str:
        """The column a table is about: the first, unless it only numbers the rows."""
        first = table.header[0]
        vals = [r.get(first, "").strip() for r in table.rows if r.get(first, "").strip()]
        # The first column holds only row numbers (1, 2, 3 ...).
        if len(table.header) > 1 and vals and all(re.fullmatch(r"\d{1,3}\.?", v) for v in vals):
            return table.header[1]
        return first

    # Pass 3: tables with no ID column, typed by what their columns name. Three cases: a people
    # table, a subject column that names a type, and a table about entities that are already
    # defined.
    def _register_by_subject(self, tables: list[Table], key: str, registry: dict) -> None:
        """Pass 3: tables without IDs, typed by what their columns are.

        - A people table (a Name column of mostly people's names): one person per name row.
        - A table whose subject column (the first, or the second when the first only
          numbers rows) names a type: one entity per row ("Party" -> organization,
          "Phase" -> milestone, "Connected system" -> system).
        - A table whose subject cells all name entities already defined (by ID or exact
          name) is about them: another column that names a type gives the new entities,
          linked to the row's entity ("Deliverable | Acceptance criterion").
        Rows get generated IDs. Sign-off tables are left to _approvals.
        """
        for t in tables:
            if t.id_col or t.mapped_to != "text only" or not t.header or not t.rows:
                continue
            if self._approval_columns(t):
                continue
            people = self._people_column(t)
            # A people table: each row with a real name becomes a person.
            if people:
                vocab_type, title_col = "person", people
            else:
                subject = self._subject_column(t)
                cells = [r.get(subject, "").strip() for r in t.rows if r.get(subject, "").strip()]
                # Every subject cell names an entity that is already defined, so this table is
                # about those entities. A column that names a type gives the new entities, linked
                # to the row's entity.
                if cells and all(self._refs(c, registry, names=True) for c in cells):
                    title_col = next(
                        (h for h in t.header if h != subject and self._type_of_header(h)), None
                    )
                else:
                    title_col = subject
                # The type of the new entities, taken from the column that holds their names.
                vocab_type = title_col and self._type_of_header(title_col)
            if not vocab_type:
                continue
            for row in t.rows:
                name = row.get(title_col, "").strip()
                # Skip empty rows, and person rows whose name does not look like a name.
                if not name or (vocab_type == "person" and not looks_like_person(name)):
                    continue
                item_id = self._new_id(vocab_type, registry)
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
                    "id_col": None,
                    "title_col": title_col,
                    "name": name,
                    "generated": True,
                    "doc_verb": self._party_verb(row, vocab_type),
                }
            types = sorted({r["type"] for r in registry.values() if r["table"] is t})
            if types:
                t.mapped_to = "entities: " + ", ".join(types)

    # Only an organization's role (Client, Vendor) links the document to it: has_client or
    # has_vendor.
    def _party_verb(self, row: dict, vocab_type: str) -> str | None:
        """has_client / has_vendor when an organization's Role cell says Client or Vendor."""
        if vocab_type != "organization":
            return None
        for h, cell in row.items():
            if header_key(h) in ("role", "party role", "type"):
                verb = self.v.document_fields.get(norm_header(cell))
                if verb and verb in self.v.verbs and self.v.verbs[verb].target == "organization":
                    return verb
        return None

    # Link the document to each party it names, using the verb found for that party's role.
    def _party_roles(self, doc: Page, registry: dict) -> None:
        for r in registry.values():
            if r.get("doc_verb"):
                doc.add_link(r["doc_verb"], r["slug"])

    # Read a change history table. Its latest version becomes the document's version, and each
    # author gets a note.
    def _version_history(self, tables: list[Table], doc: Page) -> None:
        """A change history table: the document version, and its authors as people.

        "Version | Date | Author | Changes", or any table with an Author column, a change
        column and a Version or Date column.
        """
        for t in tables:
            if t.id_col or t.mapped_to != "text only" or not t.header or not t.rows:
                continue
            keys = [header_key(h) for h in t.header]
            # A history table starts with a version column, or has an author column, a change
            # column, and a version or date column.
            is_history = keys[0] in VERSION_HEADERS or (
                any(k in AUTHOR_HEADERS for k in keys)
                and any(k in CHANGE_HEADERS for k in keys)
                and any(k in VERSION_HEADERS or k in DATE_HEADERS for k in keys)
            )
            if not is_history:
                continue
            version_col = next((h for h in t.header if header_key(h) in VERSION_HEADERS), None)
            author_col = next((h for h in t.header if header_key(h) in AUTHOR_HEADERS), None)
            # The last row is the latest version.
            latest = t.rows[-1].get(version_col, "").strip() if version_col else ""
            if latest:
                doc.fields.setdefault("version", latest)
            doc.fields["revisions"] = len(t.rows)
            # Each author in each row is recorded against that row's version.
            for row in t.rows if author_col else []:
                version = row.get(version_col or t.header[0], "").strip()
                for p in self._people_in(row.get(author_col, "")):
                    p.notes.append(f"Author of version {version} of {self._res.title}.")
            t.mapped_to = "document control"

    # Whether a scope list is 'in' or 'out' of scope. The heading is checked first, then the
    # lead-in line. Exclusion words win over inclusion words.
    def _list_scope(self, block: ListBlock) -> str | None:
        """'in' or 'out' for a scope list, from its heading, else from its lead-in line."""
        for text in (norm_header(block.heading), norm_header(block.lead_in)):
            if SCOPE_OUT_RE.search(text):
                return "out"
            if SCOPE_IN_RE.search(text):
                return "in"
        return None

    # Add one entity from a list item or heading. Its original text is kept, so the page can show
    # the source line.
    def _register(self, registry: dict, vocab_type: str, key: str, item_id: str, name: str,
                  section: int, text: str, **extra) -> None:  # fmt: skip
        slug = (
            self.entity_slug(vocab_type, key, name)
            if self.v.types[vocab_type].scope == "global"
            else self.entity_slug(vocab_type, key, item_id)
        )
        registry[item_id] = {
            "type": vocab_type,
            "slug": slug,
            "row": {"Item": text},
            "table": None,
            "section": section,
            "id_col": None,
            "title_col": "Item",
            "name": name,
            "text": text,
            **extra,
        }

    # Pass 3 for lists: each item becomes an entity when the vocabulary can type it. Items that
    # cannot be typed stay as text. A list whose every item became an entity is marked as consumed,
    # so the AI need not read it.
    def _register_list_items(self, lists: list[ListBlock], key: str, registry: dict) -> None:
        """Pass 3: list items become entities.

        An item that starts with an ID is typed by the ID's prefix, wherever it is. Any
        other item is typed by the nearest heading, only when that heading names exactly
        one type and the type is a statement (requirement, assumption, constraint, scope,
        step, ...); items under other headings stay text. Numbered steps follow each
        other. "Label: a, b, c" items whose label names a named type (systems,
        organizations, personas) also give one entity per name. IDs in a trace ("[BR-01]")
        become links when the entity pages are written.
        """
        for block in lists:
            # How many items of this list became entities.
            consumed = 0
            # A short lead-in line that names one type ("Acceptance criteria:") is closer to
            # the items than the heading and wins over it.
            lead_in = block.lead_in if len(block.lead_in.split()) <= LEAD_IN_MAX_WORDS else ""
            # The type for plain items, taken from the lead-in line or the heading, whichever names
            # exactly one type.
            heading_type = (
                self.v.single_type_for_heading(lead_in) if lead_in.endswith(":") else None
            ) or self.v.single_type_for_heading(block.heading)
            # Only statement types (concept, annotation, temporal) can come from a plain list item.
            # People and systems need a name, not a sentence.
            if heading_type and self.v.types[heading_type].primitive not in LIST_PRIMITIVES:
                heading_type = None
            # Steps are steps only when they are numbered.
            if heading_type == "process_step" and not block.ordered:
                heading_type = None
            # Scope items remember whether they are in or out of scope.
            scope = self._list_scope(block) if heading_type == "scope_item" else None
            previous = None
            # Each item becomes an entity of its own type, or stays as text.
            for raw in block.items:
                before = len(registry)
                text = normalize_ws(MARKUP_RE.sub("", raw))
                # An item that starts with an ID (such as 'FR-1.1 The system shall...') takes its
                # type from the ID's prefix, wherever it appears.
                own = LEADING_ID_RE.match(text)
                prefix_type = own and self.v.type_for_prefix(own.group("id").rsplit("-", 1)[0])
                if own and prefix_type:
                    item_id = own.group("id")
                    # Already defined by an earlier row or heading. This item only mentions it, so
                    # it adds nothing.
                    if item_id in registry:
                        continue  # defined elsewhere; this item only mentions it
                    # The name is the text after the ID, without trace references such as [BR-01].
                    name = normalize_ws(TRACE_RE.sub("", own.group("rest"))) or item_id
                    self._register(registry, prefix_type, key, item_id, name, block.section, text)
                    # Count the item as consumed when it became an entity.
                    consumed += len(registry) > before
                    continue
                # A 'Label: a, b, c' item can also give one entity per name (systems,
                # organizations).
                self._register_named_list(text, key, registry, block)
                # A plain item takes the heading's type. Given/when/then lines are acceptance
                # criteria, whatever the heading says.
                item_type = (
                    "acceptance_criterion" if GIVEN_WHEN_THEN_RE.match(text) else heading_type
                )
                if not item_type:
                    continue
                name = normalize_ws(TRACE_RE.sub("", text))
                item_id = self._new_id(item_type, registry)
                self._register(
                    registry,
                    item_type,
                    key,
                    item_id,
                    name,
                    block.section,
                    text,
                    generated=True,
                    attrs={"scope": scope} if scope and item_type == "scope_item" else {},
                    follows=previous if item_type == "process_step" else None,
                    of_section=block.section if item_type == "acceptance_criterion" else None,
                )
                previous = item_id
                # Count the item as consumed when it became an entity.
                consumed += len(registry) > before
            # Every item became an entity, so the AI need not read this list.
            if block.items and consumed == len(block.items):
                self._res.consumed_lists.append(
                    {"section": block.section, "start": block.start, "end": block.end,
                     "items": len(block.items)}
                )  # every item is an entity: the router need not send this text

    # Pass 4: a heading can itself be an item. 'US-1: Turn on refills' has an ID, and sub-headings
    # under a heading that names a persona or epic are items too.
    def _register_heading_items(self, sections, key: str, registry: dict) -> None:
        """Pass 4: headings that are items.

        A heading that starts with an ID ("US-1: Turn on refills") is an item of the type its
        prefix names. Any other sub-heading is an item when its parent heading names exactly
        one type that has named items (persona, epic, feature, use case: `heading_items` in
        the vocabulary); "### Applicant" under "Functional Requirements" is not, because
        requirements are statements, not names.
        """
        parents: list = []  # open ancestors, shallowest first
        for s in sections:
            # Close the headings that are no longer above this one.
            while parents and parents[-1].level >= s.level:
                parents.pop()
            text = normalize_ws(MARKUP_RE.sub("", s.heading))
            own = LEADING_ID_RE.match(text)
            prefix_type = own and self.v.type_for_prefix(own.group("id").rsplit("-", 1)[0])
            # A heading with an ID, such as 'US-1: Turn on refills', is an entity of the type its
            # prefix names.
            if own and prefix_type and own.group("id") not in registry:
                name = normalize_ws(own.group("rest")) or own.group("id")
                self._register(registry, prefix_type, key, own.group("id"), name, s.index, text)
                # Remember that this entity came from a heading, so its section can be linked to
                # it.
                registry[own.group("id")]["heading"] = True
            # A sub-heading under a heading that names one type with named items (persona, epic) is
            # an item of that type. 'Applicant' under 'Functional Requirements' is not, because
            # requirements are statements.
            elif parents and s.level > 0:
                parent_type = self.v.single_type_for_heading(parents[-1].heading)
                if parent_type and self.v.types[parent_type].heading_items and text:
                    item_id = self._new_id(parent_type, registry)
                    self._register(
                        registry, parent_type, key, item_id, text, s.index, text, generated=True
                    )
                    registry[item_id]["heading"] = True
            # Remember this heading as an open parent, for its own sub-headings.
            if s.level > 0:
                parents.append(s)

    # Link each user story to its persona. The persona is found from the 'As a <role>' wording in
    # the story's section.
    def _link_story_actors(self, sections, md: str, registry: dict) -> None:
        """'As a <role>, I want ...' in a story's section links the story to that persona."""
        # The personas of this document, by lower-case name.
        personas = {
            normalize_ws(r["name"]).lower(): r["slug"]
            for r in registry.values()
            if r["type"] == "persona"
        }
        for r in registry.values():
            if r["type"] != "user_story" or not r.get("heading"):
                continue
            m = STORY_ACTOR_RE.search(sections[r["section"]].text(md))
            if not m:
                continue
            # Drop 'a', 'an' or 'the', so 'an admin' matches the persona named 'Admin'.
            who = re.sub(r"^(?:an?|the)\s+", "", normalize_ws(m.group("who")).lower())
            # Only a persona that exists in this document is linked.
            if who in personas:
                r["actor"] = personas[who]

    # A 'Label: a, b, c' item whose label names a type that takes names (system, organization).
    # Each name becomes one entity.
    def _register_named_list(self, text: str, key: str, registry: dict, block) -> None:
        """'Integrations: core banking system, credit bureau.' -> one system per name."""
        m = LABEL_RE.match(text)
        label_type = m and self.v.single_type_for_heading(m.group("label"))
        if not label_type or self.v.types[label_type].primitive not in NAMED_LIST_PRIMITIVES:
            return
        # People come from tables and sign-offs, where their role is also stated, so they are
        # skipped here.
        if label_type == "person":
            return  # people are read from tables and sign-offs, where their role is stated
        # Split the list on commas, semicolons and the word 'and'.
        names = [n.strip(" .") for n in re.split(r",|;|\band\b", m.group("rest"))]
        names = [n for n in names if n]
        # A name that is too long means this is a sentence, not a list of names, so nothing is
        # made.
        if not names or any(len(n.split()) > MAX_NAME_WORDS for n in names):
            return
        for name in names:
            # Each name is registered once, even when an earlier row already has it.
            if any(r["type"] == label_type and r["name"].lower() == name.lower()
                   for r in registry.values()):  # fmt: skip
                continue
            item_id = self._new_id(label_type, registry)
            self._register(
                registry, label_type, key, item_id, name, block.section, text, generated=True
            )

    # The systems of this document, and the shared systems, whose names appear in the text. Used
    # for uses-system links.
    def _systems_named(self, text: str) -> list[str]:
        """Slugs of this document's systems whose name appears in the text.

        A one-word name must match its case ("Notify", not "notify"); a longer name
        matches in any case ("credit bureau"). No partial or fuzzy matching.
        """
        # System names from this document first, then the shared system names.
        names = {r["name"]: r["slug"] for r in self._registry.values() if r["type"] == "system"}
        names |= {g.name: g.slug for g in self._globals.values() if g.vocab_type == "system"}
        out = []
        for name, slug in names.items():
            # A multi-word name ignores case. A single word must match exactly, so 'Notify' does
            # not match 'notify'.
            flags = re.I if len(name.split()) > 1 else 0
            # Match the name as a whole word, not inside another word.
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, flags):
                out.append(slug)
        return out

    # The entities a table cell refers to: the IDs it contains. When names is True and there are no
    # IDs, the whole cell may match one entity name.
    def _refs(self, cell: str, registry: dict, names: bool = False) -> list[str]:
        """Slugs of the entities a cell refers to: by ID, or (names=True) by its whole text
        being an entity's exact name ("Final conformance report")."""
        # The IDs in the cell that are defined in this document.
        refs = [registry[r]["slug"] for r in ID_REF_RE.findall(cell) if r in registry]
        if refs or not names:
            return refs
        wanted = normalize_ws(cell).lower()
        # Only a name that matches exactly one entity counts. An ambiguous name links to nothing.
        slugs = {r["slug"] for r in registry.values() if normalize_ws(r["name"]).lower() == wanted}
        return sorted(slugs) if len(slugs) == 1 else []

    # Add each ID's name after it in a cell, so 'BO-1' reads as 'BO-1 (Grow revenue)'.
    def _describe(self, cell: str, registry: dict) -> str:
        return ID_REF_RE.sub(
            lambda m: (
                f"{m.group()} ({registry[m.group()]['name']})"
                if m.group() in registry
                else m.group()
            ),
            cell,
        )

    # The people named in a cell, as shared person contributions. Text in brackets is ignored.
    def _people_in(self, cell: str) -> list[GlobalContribution]:
        parts = [
            p.strip()
            for p in re.split(r",|;|/|\band\b|&", re.sub(r"\(.*?\)", "", cell))
            if p.strip()
        ]
        return [self.person(p) for p in parts if looks_like_person(p)]

    # Targets for a value in a link column: IDs first, then names, read according to the verb's
    # target type.
    def _link_value(self, verb: str, cell: str, registry: dict) -> list[str]:
        """Targets for a link-typed value: ID references, or names for person/org verbs."""
        refs = self._refs(cell, registry)
        if refs:
            return refs
        target = self.v.verbs[verb].target
        if target == "person":
            return [p.slug for p in self._people_in(cell)]
        # A named organization: the verb's only target, or its first one (member_of).
        if (target or next(iter(self.v.verbs[verb].to), None)) == "organization" and cell.strip():
            return [self._global("organization", cell.strip()).slug]
        # A named system becomes a shared system entity, one for each name in the cell.
        if target == "system" and cell.strip():
            return [self._system(n.strip()).slug for n in re.split(r"[,;]", cell) if n.strip()]
        return []

    # The system a name refers to. A system already defined in this document is reused.
    def _system(self, name: str) -> GlobalContribution:
        """The system a name refers to: one already defined in this document, else a new one."""
        wanted = normalize_ws(name).lower()
        for r in self._registry.values():
            if r["type"] == "system" and normalize_ws(r["name"]).lower() == wanted:
                return self._global("system", r["name"])
        return self._global("system", name)

    # The type of a page address: an entity of this document, or a shared entity it contributes to.
    def _slug_type(self, slug: str) -> str | None:
        """The vocabulary type of a page this document produces or contributes to."""
        for r in self._registry.values():
            if r["slug"] == slug:
                return r["type"]
        g = self._globals.get(slug)
        return g.vocab_type if g else None

    # ---- document control ------------------------------------------------------

    # A two-column table is a field table when its headers say so, or when most of its first column
    # holds known field labels (Document ID, Version, ...).
    def _is_field_table(self, t: Table) -> bool:
        """A two-column cover table: "Field | Value" headers, or mostly known field labels."""
        if len(t.header) != 2 or t.id_col:
            return False
        if norm_header(t.header[0]) in FIELD_HEADERS and norm_header(t.header[1]) in VALUE_HEADERS:
            return True
        labels = [norm_header(r.get(t.header[0], "")) for r in t.rows]
        # Labels the vocabulary maps, plus the standard cover labels.
        known = sum(lb in self.v.document_fields or lb in FIELD_LABELS for lb in labels)
        return bool(labels) and known * 2 >= len(labels)

    # Read every field table. Each Field | Value row becomes a document field, or a link when the
    # field names a person or organization.
    def _document_fields(self, tables: list[Table], doc: Page, registry: dict) -> None:
        for t in tables:
            if not self._is_field_table(t):
                continue
            fcol, vcol = t.header
            for row in t.rows:
                field, value = row.get(fcol, "").strip(), row.get(vcol, "").strip()
                if field and value:
                    self._set_field(doc, field, value, registry)
            t.mapped_to = "document attributes"

    # Store one field of the document: a link when the vocabulary maps the field to a verb,
    # otherwise an attribute.
    def _set_field(self, doc: Page, field: str, value: str, registry: dict, cover=False) -> None:
        """One document field: a link for a person/organization verb, else an attribute.

        On a cover line, an unmapped role label with a full name as its value ("Design lead:
        Hana Sato") also gives that person.
        """
        # The vocabulary's name for this field, if it has one.
        mapped = self.v.document_fields.get(header_key(field))
        # A field that names a link verb links to the people or organizations it names. When none
        # of them resolve, the text is kept as an attribute.
        if mapped and mapped in self.v.verbs:
            targets = self._link_value(mapped, value, registry)
            for target in targets:
                doc.add_link(mapped, target)
            if not targets:
                doc.fields[self.safe_key(field, "source_document")] = value
            return
        doc.fields[mapped or self.safe_key(field, "source_document")] = value
        # On a cover line, a role label ('Design lead') with a full name gives that person a note.
        if cover and PERSON_LABEL_RE.search(field) and len(value.split()) >= 2 and looks_like_person(value):
            self.person(value).notes.append(f"{field.strip()} of {self._res.title}.")

    # Read the cover lines ('Label: value') at the top of the document.
    def _cover_fields(self, sections, md: str, doc: Page, registry: dict) -> None:
        """"Label: value" lines on the cover: in the first sections, before any numbered
        heading or heading that names a type (bold labels, bullets or plain lines)."""
        for s in sections[:COVER_SECTIONS]:
            # Cover lines stop at the first numbered section, or at a heading that names a type.
            if s.index > 0 and not self._is_title_phrase(s.heading) and (
                re.match(r"\d", s.heading.strip()) or self.v.type_for_heading(s.heading)
            ):
                break
            for line in s.text(md).splitlines():
                m = COVER_LINE_RE.match(MARKUP_RE.sub("", line.strip()))
                # A label of five words or fewer is a field label. Longer text is a sentence.
                if m and len(m.group("label").split()) <= 5:
                    self._set_field(doc, m.group("label").strip(), m.group("value").strip(), registry, cover=True)

    # Read signature blocks written as text. Each 'For <organization>:' line starts one block. Its
    # labelled lines (Name, Title, Signature, Date) describe the signer.
    def _signature_blocks(self, sections, md: str, doc: Page) -> None:
        """Signature blocks written as text under a Signatures heading:

            For the Department of Revenue / Signature: /s/ Jo Doe / Name: Jo Doe / Title: ... / Date: ...

        Each block with a Name gives a person, a member_of link to the party named by "For ...",
        and approved when the Signature and the Date are filled in.
        """
        for s in sections:
            # Only sections under a signature heading hold signature blocks.
            if not SIGNATURE_HEADING_RE.search(s.heading):
                continue
            blocks, cur = [], None
            for line in s.text(md).splitlines()[1:]:
                text = MARKUP_RE.sub("", line.strip())
                start = SIGN_FOR_RE.match(line.strip())
                if start:
                    cur = {"org": normalize_ws(start.group("org")), "labels": {}}
                    blocks.append(cur)
                    continue
                m = COVER_LINE_RE.match(text)
                if m and cur is not None:
                    cur["labels"][header_key(m.group("label"))] = m.group("value").strip()
            for b in blocks:
                labels = b["labels"]
                name = labels.get("name", "")
                # A block without a person's name has no signer, so it is skipped.
                if not looks_like_person(name):
                    continue
                p = self.person(name)
                title = labels.get("title") or labels.get("position")
                if title:
                    p.attributes.setdefault("job_title", title)
                # The signer belongs to the organization named in 'For ...'.
                p.add_link("member_of", self._global("organization", b["org"]).slug)
                # A signed and dated block means the person approved this document.
                if labels.get("signature") and labels.get("date"):
                    p.add_link("approved", doc.slug)
                p.notes.append(
                    f"Signed for {b['org']} in {self._res.title}: "
                    + "; ".join(f"{k}: {v}" for k, v in labels.items() if v)
                )

    # ---- entity pages -----------------------------------------------------------

    # Pass 5: build the page for each registered entity, with its attributes and links. Shared
    # types go to the shared contribution instead, which is written once for all documents.
    def _entity_pages(
        self, tables, sections, md, key, kind, doc_slug, registry, doc_title
    ) -> None:
        for item_id, r in registry.items():
            vocab_type, row, t = r["type"], r["row"], r["table"]
            # The section the entity came from: its table's section, or its own.
            section = sections[t.section if t else r["section"]]
            # Attributes (plain cell values) and links for this entity. They are collected column
            # by column below.
            attrs, links = dict(r.get("attrs") or {}), {}
            # The body lines: the original list text, then one line per column.
            lines = [r["text"]] if r.get("text") else []
            for col, cell in row.items():
                if col == r["id_col"] or not cell.strip():
                    continue
                if col == r["title_col"]:
                    # The title stays the name; IDs inside it ("... (BO-1)") still link.
                    for target in self._refs(cell, registry):
                        self._add_typed_link(links, None, vocab_type, target, r["slug"])
                    continue
                # A column whose name is a known verb (such as 'Depends on') makes links. Any other
                # column is checked for IDs or names and links with the generic verb.
                column_verb = self.v.verb_for_column(col)
                targets = (
                    self._link_value(column_verb, cell, registry)
                    if column_verb
                    else self._refs(cell, registry, names=True)
                )
                if targets:
                    for target in targets:
                        verb = self._add_typed_link(
                            links, column_verb, vocab_type, target, r["slug"]
                        )
                        self._column_gap(
                            col, column_verb, verb, r, item_id, vocab_type, target, cell
                        )
                    lines.append(f"- **{col}:** {self._describe(cell, registry)}")
                else:
                    # A cell that makes no link is an attribute of the entity.
                    attrs[self.safe_key(col, vocab_type)] = cell
                    lines.append(f"- **{col}:** {cell}")
            # A process step points back to the step before it.
            if r.get("follows"):
                links.setdefault("follows", []).append(registry[r["follows"]]["slug"])
            # A user story is performed by its persona.
            if r.get("actor"):
                self._add_typed_link(links, "performed_by", vocab_type, r["actor"], r["slug"])
            # An acceptance criterion belongs to the item of the heading above it.
            if r.get("of_section") is not None:
                owner = next(
                    (o for o in registry.values() if o.get("heading") and o["section"] == r["of_section"]),
                    None,
                )
                if owner:
                    self._add_typed_link(links, None, vocab_type, owner["slug"], r["slug"])
            # An entity whose text names one of this document's systems gets a uses_system link to
            # it.
            if vocab_type != "system" and self.v.fits("uses_system", vocab_type, "system"):
                for target in self._systems_named(" ".join(row.values())):
                    self._add_typed_link(links, "uses_system", vocab_type, target, r["slug"])

            # The context line, which says where the entity came from.
            context = f"From {doc_title} ({key}), section {section.index + 1}: {section.heading}."
            if self.v.types[vocab_type].scope == "global":
                g = self._global(vocab_type, r["name"])
                g.attributes.update(attrs)
                # A row that had its own ID in the document keeps that ID as an attribute, so
                # search can find it.
                if not r.get("generated"):
                    g.attributes.setdefault("item_id", item_id)
                for verb, targets in links.items():
                    for target in targets:
                        g.add_link(verb, target)
                # The section and the text go into the shared entity's notes, so a reader sees
                # where each mention came from.
                g.notes += [
                    f"{item_id} in {doc_title} ({key}), "
                    f"section {section.index + 1}: {section.heading}.",
                    *lines,
                ]
                continue
            # The page for this entity. Its fields hold the ID, the document, the section and the
            # attributes.
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
            # Each entity links back to the document it came from.
            page.add_link("defined_in", doc_slug)
            for verb, targets in links.items():
                for target in targets:
                    page.add_link(verb, target)
            page.body = "\n".join([f"# {item_id}: {r['name']}", "", context, "", *lines]) + "\n"
            # Add the page to the document's list of pages.
            self._res.pages.append(page)

    # Pick a verb that fits both the source and the destination type, then add the link. Returns
    # the verb used, or None for a self-link.
    def _add_typed_link(
        self, links: dict, column_verb, src_type: str, target: str, own: str
    ) -> str | None:
        """Add a link whose verb fits both page types (see Vocabulary.choose_verb).

        Returns the verb used, or None for a self-link.
        """
        # A link to itself is skipped.
        if target == own:
            return None
        dst_type = self._slug_type(target)
        verb = (
            self.v.choose_verb(column_verb, src_type, dst_type)
            if dst_type
            else column_verb or "references"
        )
        # Each target is added once per verb.
        if target not in links.get(verb, []):
            links.setdefault(verb, []).append(target)
        return verb

    # Decide whether a table is a sign-off table, and find its decision and approver columns.
    def _approval_columns(self, t: Table) -> tuple[str | None, str] | None:
        """(decision column, approver column) when the table is a sign-off table.

        A table without a decision column is a sign-off table when it sits under a
        Signatures heading and has a date column; its decision column is then None.
        """
        # Map each normalised header back to the header as written.
        headers = {norm_header(h): h for h in t.header}
        decision = next((h for n, h in headers.items() if n in APPROVAL_HEADERS), None)
        approver = next((h for n, h in headers.items() if n in APPROVER_HEADERS), None)
        # A table with an approver column but no decision column may still be a sign-off table,
        # when it sits under a signatures heading and has a date column.
        if approver and not decision:
            heading = self._res.sections[t.section].heading
            if SIGNATURE_HEADING_RE.search(heading) and any(n in DATE_HEADERS for n in headers):
                return None, approver
        return (decision, approver) if decision and approver else None

    # Pass 6: read the sign-off tables. Each approver becomes a person, approved when the decision
    # says so, or when the signature and date are filled in.
    def _approvals(self, tables, doc: Page, registry) -> None:
        doc_slug = doc.slug
        for t in tables:
            if t.id_col or t.mapped_to != "text only":
                continue
            columns = self._approval_columns(t)
            if not columns:
                continue
            decision, approver = columns
            date_col = next((h for h in t.header if norm_header(h) in DATE_HEADERS), None)
            # A signature column, or a signatures table without one, means "signed" when
            # filled in (and dated, if there is a date column) rather than a status word.
            signed = decision is None or norm_header(decision) in SIGNATURE_HEADERS
            for row in t.rows:
                cell = row.get(approver, "")
                role = None
                # A cell 'Name — Role' gives the person and their role.
                named = NAME_ROLE_RE.match(cell.strip())
                if named and looks_like_person(named.group("name").strip()):
                    people = [self.person(named.group("name").strip())]
                    role = named.group("role").strip()
                else:
                    # The approver is either an entity defined in this document, or a name written
                    # in the cell.
                    people = [
                        self._global_for_slug(s) for s in self._refs(cell, registry)
                    ] or self._people_in(cell)
                # The decision column gives the status word. Without one, the row means signed.
                status = row.get(decision, "").strip() if decision else "signed"
                # A signed row is approved when it has a signature and, if there is a date column,
                # a date too.
                if signed:
                    dated = not date_col or bool(row.get(date_col, "").strip())
                    approved = bool(status) and dated and not NOT_APPROVED_RE.search(status)
                else:
                    # A status counts as approved only when it has an approved word and no negative
                    # word such as 'pending'.
                    approved = bool(APPROVED_RE.search(status)) and not NOT_APPROVED_RE.search(
                        status
                    )
                for p in people:
                    if p is None:
                        continue
                    if role:
                        p.attributes.setdefault("job_title", role)
                    for h, v in row.items():
                        if h == approver or not v.strip():
                            continue
                        if header_key(h) in ("title", "job title", "position", "role"):
                            p.attributes.setdefault("job_title", v.strip())
                        # A column that means membership (such as 'Company') links the approver to
                        # that organization.
                        if self.v.verb_for_column(h) == "member_of":
                            for org in self._link_value("member_of", v, registry):
                                p.add_link("member_of", org)
                    # A row marked as sponsor makes this person the document's sponsor.
                    if any(SPONSOR_RE.search(v) for h, v in row.items() if h != approver):
                        doc.add_link("sponsored_by", p.slug)
                    # An approved person links to the document with 'approved'.
                    if approved:
                        p.add_link("approved", doc_slug)
                    # Every other column of the row is kept as an attribute of the person, named
                    # 'approval ...'.
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

    # The shared contribution already recorded under this page address, if there is one.
    def _global_for_slug(self, slug: str) -> GlobalContribution | None:
        return self._globals.get(slug)

    # ---- email / transcript / chat ------------------------------------------------

    # Email senders and recipients, or chat and transcript speakers, become people linked to the
    # document.
    def _participants(self, conv: Converted, kind: str, doc: Page) -> None:
        meta = conv.meta
        if kind == "email":
            # Each email header maps to one link: sent_by, sent_to or copied_to.
            for field, verb in (("from", "sent_by"), ("to", "sent_to"), ("cc", "copied_to")):
                for name, addr in meta.get(field, []):
                    if not (name or addr):
                        continue
                    p = self.person(person_name_from_address(name, addr))
                    # Keep each address once, in lower case, as an email attribute.
                    if addr:
                        emails = p.attributes.setdefault("email", [])
                        if addr.lower() not in emails:
                            emails.append(addr.lower())
                    doc.add_link(verb, p.slug)
                    p.notes.append(
                        f'{verb.replace("_", " ").capitalize()} the email "{self._res.title}".'
                    )
        # Speakers who look like names (or are a single word) become participants.
        elif kind in ("transcript", "chat"):
            for name in meta.get("participants", []):
                if looks_like_person(name) or len(name.split()) == 1:
                    p = self.person(name)
                    doc.add_link("has_participant", p.slug)
                    p.notes.append(f'Participant in the {kind} "{self._res.title}".')

    # ---- sections -------------------------------------------------------------

    # Pass 7: one page per section, holding its full text. The page links to its document, and to
    # every entity whose ID the section mentions.
    def _section_pages(self, sections, md, key, kind, doc_slug, doc_title, registry) -> list[Page]:
        pages = []
        # Pad the section numbers to the same width, so the pages sort in order (01, 02, ... 10).
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
            # A section belongs to its document.
            page.add_link("section_of", doc_slug)
            # Every ID the section mentions links to its entity as 'mentions'.
            for ref in ID_REF_RE.findall(text):
                if ref in registry:
                    page.add_link("mentions", registry[ref]["slug"])
            pages.append(page)
        return pages

    # Write the document page's text: the title, the source, the attributes, the sections, and a
    # count of what was extracted.
    def _doc_body(self, res: DocumentResult, doc: Page) -> str:
        counts: dict[str, int] = {}
        for p in res.pages:
            counts[p.type] = counts.get(p.type, 0) + 1
        # Shared entities count towards the extracted items too.
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
