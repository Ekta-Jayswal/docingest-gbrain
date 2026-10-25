# What this file does: audits what each section of one document links to, and writes the audit as one
# HTML page. `./ingest.sh gap-audit <file>` calls it. It reads the same manifest that ingest writes,
# so the numbers match what the brain would get, and the brain is not touched.

"""Gap audit for one document.

For each section: the items it names, the typed links those items make, and the tables and prose it
leaves unlinked. For each item: the sections that name it, its typed links out and in, and whether
it has any typed link. Items and shared people or systems named in more than one section are listed
with the first and the last section they appear in. `find` lists every section a piece of text
appears in, so a detail stated in two places can be checked in both.
"""

from __future__ import annotations

import html
import re
from collections import Counter, defaultdict
from pathlib import Path

from docingest.reporting import document_graph
from docingest.utils.data_types import DocumentResult
from docingest.vocabulary.vocabulary_loader import Vocabulary

# A section at least this long, with no items and no tables in it, is reported as prose that no
# item was taken from.
PROSE_CHARS = 300
# The verbs that only say where a page belongs. They are not typed links.
STRUCTURAL = {"defined_in", "section_of", "mentions_item", "subsection_of", "appears_in", "attachment_of"}
# How much text a find result shows around the match.
SNIPPET = 90


# A pattern for a name or a phrase: whole words, any case, any whitespace between the words.
def _pattern(text: str) -> re.Pattern:
    """A case-insensitive pattern that matches the words of ``text`` as whole words."""
    words = text.split()
    body = r"\s+".join(re.escape(w) for w in words)
    left = r"(?<![\w-])" if words[0][:1].isalnum() else ""
    right = r"(?![\w-])" if words[-1][-1:].isalnum() else ""
    return re.compile(left + body + right, re.I)


# Build the audit from a manifest. ``shared`` maps the slug of each shared person, system or term to
# (vocabulary type, name); ``find`` is a phrase to look for in every section.
def analyse(manifest: dict, vocab: Vocabulary, shared: dict | None = None, find: str | None = None) -> dict:
    """The numbers and rows of the audit, as plain data."""
    shared = shared or {}
    pages = {p["slug"]: p for p in manifest["pages"]}
    sections = manifest["sections"]
    by_index = {s["index"]: s for s in sections}
    index_of = {s["slug"]: s["index"] for s in sections}
    structural = {vocab.canonical_verb(v) for v in STRUCTURAL}
    mention = vocab.canonical_verb("mentions_item")
    items = {slug for slug, p in pages.items() if p["type"] not in document_graph.NOT_ITEMS}

    # Who names what. A section names an item through its mention links; the item's own typed links
    # are read from the item's page.
    mentioned: dict[str, set[int]] = defaultdict(set)
    parent: dict[int, int] = {}
    for s in sections:
        for verb, targets in pages.get(s["slug"], {}).get("links", {}).items():
            v = vocab.canonical_verb(verb)
            if v == mention:
                for t in targets:
                    mentioned[t].add(s["index"])
            if v == "subsection_of" and targets:
                parent[s["index"]] = index_of.get(targets[0], -1)
    typed_out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    typed_in: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for slug in items:
        for verb, targets in pages[slug].get("links", {}).items():
            v = vocab.canonical_verb(verb)
            if v in structural:
                continue
            for t in targets:
                typed_out[slug].append((v, t))
                typed_in[t].append((v, slug))

    def label(slug: str) -> str:
        if slug in pages:
            return pages[slug]["title"]
        if slug in shared:
            return shared[slug][1]
        return slug.rsplit("/", 1)[-1].replace("-", " ").title()

    def sec_label(i: int) -> str:
        # The position is shown too, since a heading may already start with its own number.
        return f"{by_index[i]['heading']} (section {i + 1})"

    def kind(slug: str) -> str:
        if slug in pages:
            return pages[slug]["type"].replace("_", " ")
        return shared[slug][0].replace("_", " ") if slug in shared else "shared"

    bodies = {s["index"]: pages.get(s["slug"], {}).get("body", "") for s in sections}
    # A section names an item when the item is defined there, or its ID is written in the section's
    # text. The pipeline's section links are wider: a parent section links to the items of its
    # sub-sections too, so those links are not used to say what a section names.
    defined_at = {f"{s['index'] + 1}. {s['heading']}": s["index"] for s in sections}
    named: dict[str, set[int]] = {}
    for slug in items:
        found = set()
        section_name = pages[slug]["fields"].get("section")
        if section_name in defined_at:
            found.add(defined_at[section_name])
        ident = pages[slug]["fields"].get("item_id")
        if ident:
            pat = re.compile(rf"(?<![\w-]){re.escape(ident)}(?![\w-])")
            found |= {i for i in bodies if pat.search(bodies[i])}
        named[slug] = found

    # One row per item: where it is named, and its typed links both ways.
    item_rows = []
    for slug in sorted(items, key=lambda x: (pages[x]["type"], label(x))):
        secs = sorted(named.get(slug, ()))
        item_rows.append({
            "label": label(slug),
            "type": kind(slug),
            "sections": [sec_label(i) for i in secs],
            "first": sec_label(secs[0]) if secs else None,
            "last": sec_label(secs[-1]) if secs else None,
            "out": [(v, label(t)) for v, t in typed_out.get(slug, [])],
            "in": [(v, label(t)) for v, t in typed_in.get(slug, [])],
            "typed": bool(typed_out.get(slug) or typed_in.get(slug)),
        })

    # Things named in more than one section: items by their IDs, shared people, systems and terms by
    # their names in the text.
    repeated = []
    for slug in items:
        secs = sorted(named.get(slug, ()))
        if len(secs) > 1:
            repeated.append(_repeat(label(slug), kind(slug), secs, sec_label, "named by ID"))
    for slug, (_, name) in shared.items():
        secs = [i for i in sorted(bodies) if _pattern(name).search(bodies[i])]
        if len(secs) > 1:
            repeated.append(_repeat(name, kind(slug), secs, sec_label, "named in the text"))

    # One row per section: what it names, what those items link to, and what it leaves unlinked.
    tables_by_section: dict[int, list[dict]] = defaultdict(list)
    for t in manifest["tables"]:
        tables_by_section[t["section"]].append(t)
    children = Counter(parent.values())
    section_rows = []
    for s in sections:
        i = s["index"]
        here = [slug for slug in items if i in named.get(slug, ())]
        typed_here = [slug for slug in here if typed_out.get(slug) or typed_in.get(slug)]
        tables = tables_by_section.get(i, [])
        text_tables = [t for t in tables if t["mapped_to"] == "text only"]
        chars = s["end"] - s["start"]
        flags = []
        if text_tables:
            flags.append(f"{len(text_tables)} table(s) kept as text")
        if len(typed_here) < len(here):
            flags.append(f"{len(here) - len(typed_here)} item(s) with no typed link")
        if not here and not tables and chars >= PROSE_CHARS and not children[i]:
            flags.append("prose with no items")
        section_rows.append({
            "index": i,
            "label": sec_label(i),
            "level": s["level"],
            "chars": chars,
            "items": [label(x) for x in here],
            "typed": len(typed_here),
            "links_out": sum(len(typed_out.get(x, [])) for x in here),
            "tables": [(", ".join(t["header"]), t["mapped_to"], len(t["rows"])) for t in tables],
            "parent": sec_label(parent[i]) if parent.get(i, -1) in by_index else None,
            "children": children[i],
            "flags": flags,
        })

    # Every gap, in one list: a table kept as text, prose with nothing taken from it, an item with
    # no typed link.
    gaps = []
    for r in section_rows:
        for t in r["tables"]:
            if t[1] == "text only":
                gaps.append({"where": r["label"], "detail": f"table ({t[0]}; {t[2]} rows) kept as text"})
        if "prose with no items" in r["flags"]:
            gaps.append({"where": r["label"], "detail": f"{r['chars']} characters of prose, no items taken from it"})
    for r in item_rows:
        if not r["typed"]:
            gaps.append({"where": r["type"], "detail": f"{r['label']} has no typed link", "sections": r["sections"]})

    # Items per type: how many, how many are linked, and which verbs they use.
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in item_rows:
        by_type[r["type"]].append(r)
    type_rows = []
    for t, rows in sorted(by_type.items()):
        type_rows.append({
            "type": t,
            "count": len(rows),
            "typed": sum(r["typed"] for r in rows),
            "out": Counter(v for r in rows for v, _ in r["out"]),
            "in": Counter(v for r in rows for v, _ in r["in"]),
            "unlinked": [r["label"] for r in rows if not r["typed"]],
        })

    found = _find(find, sections, bodies, sec_label, items, label) if find else None
    summary = {
        "sections": len(sections),
        "items": len(items),
        "items_typed": sum(r["typed"] for r in item_rows),
        "tables": len(manifest["tables"]),
        "tables_text": sum(t["mapped_to"] == "text only" for t in manifest["tables"]),
        "repeated": len(repeated),
        "gaps": len(gaps),
        "typed_links": sum(len(v) for v in typed_out.values()),
    }
    return {
        "summary": summary,
        "sections": section_rows,
        "items": item_rows,
        "types": type_rows,
        "repeated": repeated,
        "gaps": gaps,
        "found": found,
        "inferred": (manifest.get("llm_report") or {}).get("inferred_links", []),
    }


# One row for a name that more than one section names: where it first and last appears.
def _repeat(name: str, kind_: str, secs: list[int], sec_label, how: str) -> dict:
    return {
        "name": name,
        "kind": kind_,
        "first": sec_label(secs[0]),
        "last": sec_label(secs[-1]),
        "sections": [sec_label(i) for i in secs],
        "how": how,
    }


# Every section a phrase appears in, with the first match around it. Also the items whose titles hold
# the phrase.
def _find(text: str, sections: list[dict], bodies: dict, sec_label, items: set, label) -> dict:
    """Where a phrase appears: each section with a count and a snippet, and the items that name it."""
    pat = _pattern(text)
    hits = []
    for s in sections:
        body = bodies[s["index"]]
        matches = list(pat.finditer(body))
        if matches:
            m = matches[0]
            start = max(0, m.start() - SNIPPET // 2)
            # Table pipes read badly in a sentence, so they become separators.
            snippet = " ".join(re.sub(r"\|", " · ", body[start : m.end() + SNIPPET // 2]).split())
            hits.append({"section": sec_label(s["index"]), "count": len(matches), "snippet": snippet})
    return {
        "text": text,
        "sections": hits,
        "first": hits[0]["section"] if hits else None,
        "last": hits[-1]["section"] if hits else None,
        "items": sorted(label(x) for x in items if pat.search(label(x))),
    }


# The text summary the command prints.
def summary_lines(a: dict, page: Path | None = None) -> list[str]:
    """The numbers, the repeated names, the find result and the gaps, as lines of text."""
    s = a["summary"]
    lines = [
        f"{s['sections']} sections, {s['items']} items ({s['items_typed']} with a typed link), "
        f"{s['typed_links']} typed links, {s['tables_text']} of {s['tables']} tables kept as text.",
    ]
    if a["repeated"]:
        lines += ["", f"Named in more than one section ({len(a['repeated'])}):"]
        for r in a["repeated"]:
            lines.append(f"  {r['name']} ({r['kind']}): first in {r['first']}; last in {r['last']}; {len(r['sections'])} sections")
    if a["found"]:
        f = a["found"]
        lines += ["", f"Find \"{f['text']}\": {len(f['sections'])} section(s); first in {f['first']}; last in {f['last']}"]
        for h in f["sections"]:
            lines.append(f"  {h['section']} ({h['count']}x): {h['snippet']}")
        if f["items"]:
            lines.append(f"  items with this in the title: {', '.join(f['items'])}")
    if a["gaps"]:
        lines += ["", f"Gaps ({len(a['gaps'])}):"]
        for g in a["gaps"][:40]:
            lines.append(f"  {g['where']}: {g['detail']}")
        if len(a["gaps"]) > 40:
            lines.append(f"  ... {len(a['gaps']) - 40} more on the page")
    if page:
        lines += ["", f"Page: {page}"]
    return lines


# Escape text for the page, keeping None as an empty cell.
def _e(value) -> str:
    return html.escape("" if value is None else str(value))


# The page styles: the colours (light and dark), the layout and the tables. The graph's own styles
# are added after these.
PAGE_CSS = """
:root{--bg:#fff;--panel:#f7f7f9;--ink:#1d1d22;--quiet:#6b6b76;--rule:#dcdce2;--accent:#2b6cb0;--warn:#a93a28;--good:#4a7a35;--gapbg:#fdf0ee}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#16161a;--panel:#202026;--ink:#ececf1;--quiet:#9a9aa6;--rule:#34343c;--accent:#6aa7e8;--warn:#e8806c;--good:#86c26b;--gapbg:#3a2220}}
:root[data-theme="dark"]{--bg:#16161a;--panel:#202026;--ink:#ececf1;--quiet:#9a9aa6;--rule:#34343c;--accent:#6aa7e8;--warn:#e8806c;--good:#86c26b;--gapbg:#3a2220}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 80px}
nav{margin:0 0 8px;color:var(--quiet);font-size:13px}nav a{color:var(--accent)}
.doc{margin-top:48px;border-top:2px solid var(--rule);padding-top:8px}
h1{font-size:23px;margin:12px 0 4px}h2{font-size:18px;margin:34px 0 4px;border-top:1px solid var(--rule);padding-top:18px}
h3{font-size:15px;margin:22px 0 6px}.lead,.hint{color:var(--quiet);margin:2px 0 10px}
.kpis{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.kpis div{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:9px 13px;flex:1;min-width:170px}
.kpis b{display:block;font-size:19px}.kpis span{color:var(--quiet);font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px;display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--rule);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--quiet);font-weight:600}td.n{text-align:right;white-space:nowrap}tr.gap td{background:var(--gapbg)}
.gwrap{overflow-x:auto}li{margin:4px 0}code{background:var(--panel);padding:1px 5px;border-radius:4px}
"""


# The body of one document's audit: the summary, the graph, every section, the repeated names, the
# find result, the items by type, the gaps, every item, and the links the AI inferred.
def _document_html(manifest: dict, a: dict) -> str:
    """One document's audit as a section of the page."""
    s = a["summary"]
    graph = document_graph.render(manifest)
    graph_html = graph[0] if graph else "<p class='hint'>No items to draw.</p>"
    pct = f"{round(100 * s['items_typed'] / s['items'])}%" if s["items"] else "—"
    kpis = "".join(
        f"<div><b>{_e(v)}</b><span>{_e(k)}</span></div>"
        for k, v in [
            ("sections", s["sections"]),
            ("items with a typed link", f"{s['items_typed']} of {s['items']} ({pct})"),
            ("typed links", s["typed_links"]),
            ("tables kept as text", f"{s['tables_text']} of {s['tables']}"),
            ("named in more than one section", s["repeated"]),
            ("gaps", s["gaps"]),
        ]
    )
    sec_rows = "".join(
        f"<tr class='{'gap' if r['flags'] else ''}'><td>{'&nbsp;&nbsp;' * max(r['level'] - 1, 0)}{_e(r['label'])}</td>"
        f"<td class='n'>{len(r['items'])}</td><td class='n'>{r['typed']}</td><td class='n'>{r['links_out']}</td>"
        f"<td>{_e('; '.join(r['items'][:6]) + (' …' if len(r['items']) > 6 else ''))}</td>"
        f"<td>{_e('; '.join(f'{h} → {m}' for h, m, _ in r['tables']))}</td>"
        f"<td>{_e('; '.join(r['flags']))}</td></tr>"
        for r in a["sections"]
    )
    rep_rows = "".join(
        f"<tr><td>{_e(r['name'])}</td><td>{_e(r['kind'])}</td><td>{_e(r['first'])}</td><td>{_e(r['last'])}</td>"
        f"<td class='n'>{len(r['sections'])}</td><td>{_e(r['how'])}</td></tr>"
        for r in a["repeated"]
    ) or "<tr><td colspan=6>Nothing is named in more than one section.</td></tr>"
    type_rows = "".join(
        f"<tr class='{'gap' if t['typed'] < t['count'] else ''}'><td>{_e(t['type'])}</td><td class='n'>{t['count']}</td>"
        f"<td class='n'>{t['typed']}</td><td>{_e(', '.join(f'{v} ×{c}' for v, c in t['out'].items()) or '—')}</td>"
        f"<td>{_e(', '.join(f'{v} ×{c}' for v, c in t['in'].items()) or '—')}</td>"
        f"<td>{_e(', '.join(t['unlinked'][:8]) + (' …' if len(t['unlinked']) > 8 else ''))}</td></tr>"
        for t in a["types"]
    )
    item_rows = "".join(
        f"<tr class='{'' if r['typed'] else 'gap'}'><td>{_e(r['label'])}</td><td>{_e(r['type'])}</td>"
        f"<td>{_e(r['first'] or '—')}</td><td>{_e(r['last'] or '—')}</td>"
        f"<td>{_e('; '.join(f'{v} {t}' for v, t in r['out']) or '—')}</td>"
        f"<td>{_e('; '.join(f'{t} {v}' for v, t in r['in']) or '—')}</td></tr>"
        for r in a["items"]
    )
    gap_items = "".join(f"<li><b>{_e(g['where'])}</b>: {_e(g['detail'])}</li>" for g in a["gaps"]) or "<li>No gaps.</li>"
    found_html = ""
    if a["found"]:
        f = a["found"]
        hits = "".join(
            f"<tr><td>{_e(h['section'])}</td><td class='n'>{h['count']}</td><td>{_e(h['snippet'])}</td></tr>"
            for h in f["sections"]
        ) or "<tr><td colspan=3>Not found in any section.</td></tr>"
        found_html = (
            f"<h2>Find: “{_e(f['text'])}”</h2>"
            f"<p class='lead'>{len(f['sections'])} section(s). First in {_e(f['first'] or '—')}; last in {_e(f['last'] or '—')}.</p>"
            f"<table><tr><th>Section</th><th>Matches</th><th>Text around the first match</th></tr>{hits}</table>"
            + (f"<p class='hint'>Items with this in the title: {_e(', '.join(f['items']))}</p>" if f["items"] else "")
        )
    inferred_html = ""
    if a["inferred"]:
        rows = "".join(
            f"<tr><td>{_e(r['from'])}</td><td>{_e(r['verb'])}</td><td>{_e(r['to'])}</td><td>{_e(r['reason'])}</td></tr>"
            for r in a["inferred"]
        )
        inferred_html = (
            "<h2>Links the AI inferred across sections</h2>"
            "<p class='lead'>Each one quotes both ends in the text; the item page says so.</p>"
            f"<table><tr><th>From</th><th>Verb</th><th>To</th><th>Why</th></tr>{rows}</table>"
        )
    return f"""<section class="doc" id="{_e(manifest['key'])}">
<h1>Gap audit: {_e(manifest['title'])}</h1>
<p class="lead">{_e(manifest['source_path'].rsplit('/', 1)[-1])} · {_e(manifest['kind'].upper())} · converted with {_e(manifest['converter'])}.
Rules only unless the page says the AI ran. Red rows are gaps. The brain is not touched.</p>
<div class="kpis">{kpis}</div>
<h2>Graph: sections → items → shared people and systems</h2>
<p class="hint">Hover a box to light up its links. The lines are the pipeline's section links, which also
include a parent section's links to its sub-sections' items. Thick outline: an item named by more than one section.</p>
<div class="gwrap">{graph_html}</div>
<h2>Every section</h2>
<p class="hint">Items named: the item is defined in the section, or its ID is written in the section's text.</p>
<table><tr><th>Section</th><th>Items named</th><th>Typed</th><th>Links out</th><th>Which items</th><th>Tables (header → became)</th><th>Gap</th></tr>{sec_rows}</table>
<h2>Named in more than one section</h2>
<p class="lead">The first and the last section that name each one.</p>
<table><tr><th>Name</th><th>Type</th><th>First section</th><th>Last section</th><th>Sections</th><th>How it is named</th></tr>{rep_rows}</table>
{found_html}
<h2>Items by type</h2>
<table><tr><th>Type</th><th>Count</th><th>Typed</th><th>Links it makes</th><th>Links it receives</th><th>No typed link</th></tr>{type_rows}</table>
<h2>Gaps</h2><ul>{gap_items}</ul>
<h2>Every item</h2>
<table><tr><th>Item</th><th>Type</th><th>First section</th><th>Last section</th><th>Links out</th><th>Links in</th></tr>{item_rows}</table>
{inferred_html}
</section>"""


# The whole page: one section per document, with a list of links to them when there are several.
def render_page(parts: list[tuple[dict, dict]]) -> str:
    """The audit page for one or more documents, each a section of the page."""
    nav = ""
    if len(parts) > 1:
        nav = "<nav>Documents: " + " · ".join(
            f'<a href="#{_e(m["key"])}">{_e(m["title"][:60])}</a>' for m, _ in parts
        ) + "</nav>"
    docs = "\n".join(_document_html(m, a) for m, a in parts)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gap audit</title><style>{PAGE_CSS}{document_graph.CSS}</style><main>{nav}
{docs}
</main><script>{document_graph.JS}</script></html>
"""


# Build the audit of one converted document: its manifest and its analysis.
def build(res: DocumentResult, vocab: Vocabulary, find: str | None = None) -> tuple[dict, dict]:
    """The manifest the ingest would write for this document, and its audit."""
    from docingest.ingestion.pipeline import document_manifest

    manifest = document_manifest(res, vocab.gbrain_type("document_section"))
    shared = {g.slug: (g.vocab_type, g.name) for g in res.globals}
    return manifest, analyse(manifest, vocab, shared, find)


# Write the audit page for one or more documents, in the order given.
def write_page(parts: list[tuple[dict, dict]], out: Path) -> Path:
    """Write the page to ``out``; returns the path."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(parts), encoding="utf-8")
    return out


# Analyse one converted document and write its audit page. Returns the analysis for the summary.
def write(res: DocumentResult, vocab: Vocabulary, out: Path, find: str | None = None) -> dict:
    """Build the audit for one document and write its page to ``out``."""
    manifest, a = build(res, vocab, find)
    write_page([(manifest, a)], out)
    return a
