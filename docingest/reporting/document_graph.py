# What this file does: draws the section graph of one document as an inline SVG for the dashboard.
# It reads the document's manifest, so it needs no gbrain call and the page stays one static file.

"""The section graph of one document: sections, the items found in them, and the shared people.

Sections are on the left in reading order, the items found in them in the middle, and the shared
people and companies on the right. A grey line means a section mentions an item. A coloured arc is
a typed link (owned_by, verifies, references, ...). An item that more than one section mentions has a
bold outline, which shows that the sections are connected through it.
"""

from __future__ import annotations

import html
import re
from collections import defaultdict

# Pages that are not drawn as items, and the links that only say where a page belongs.
NOT_ITEMS = {"source_document", "document_section"}
STRUCTURAL_VERBS = {"section_of", "defined_in", "mentions_item", "appears_in"}

# Items past this many are left out, fewest links first, so a large document stays readable.
MAX_ITEMS = 60

ROW = 44  # vertical space for one box
COLUMN_X = {"section": 16, "item": 360, "shared": 760}
COLUMN_W = {"section": 250, "item": 280, "shared": 170}
WIDTH = 940

# Link colours, one per verb in alphabetical order. They match the page's palette.
VERB_COLORS = ["#2b6cb0", "#a93a28", "#4a7a35", "#a8710f", "#6a4fa3", "#8a4b2c"]

# Styles for the graph. Colours come from the page's variables.
CSS = """
.gv{width:100%;height:auto;max-width:940px;background:var(--bg);border:1px solid var(--rule);border-radius:8px}
.gv .cap{font:600 10px sans-serif;letter-spacing:.1em;fill:var(--quiet)}
.gv .t{font:500 12px sans-serif;fill:var(--ink)}.gv .s{font:11px sans-serif;fill:var(--quiet)}
.gv rect{fill:var(--panel);stroke:var(--quiet);stroke-width:1.2}
.gv .node.req rect{stroke:var(--accent)}.gv .node.risk rect{stroke:var(--warn)}
.gv .node.shared rect{stroke:var(--good)}.gv .node.sec rect{stroke:var(--rule)}
.gv .node.multi rect{stroke-width:3}.gv .node.ai rect{stroke-dasharray:5 3}
.gv .e{fill:none;stroke:var(--rule);stroke-width:1.2}.gv .e.multi{stroke:var(--accent);stroke-width:1.8;opacity:.7}
.gv .e.v{stroke-width:2}.gv .vl{font:600 11px sans-serif}
.gv.hov .e{opacity:.07}.gv.hov .e.on{opacity:1}.gv.hov .node{opacity:.35}.gv.hov .node.on{opacity:1}
"""

# Hover a box to light up its links. Works on every graph on the page.
JS = """
document.querySelectorAll('svg.gv').forEach(function(svg){
  function node(id){return svg.querySelector('.node[data-id="'+id+'"]')}
  svg.querySelectorAll('.node').forEach(function(n){
    n.addEventListener('mouseenter',function(){
      svg.classList.add('hov');n.classList.add('on');
      svg.querySelectorAll('.e').forEach(function(e){
        if(e.dataset.a===n.dataset.id||e.dataset.b===n.dataset.id){
          e.classList.add('on');node(e.dataset.a).classList.add('on');node(e.dataset.b).classList.add('on')}})});
    n.addEventListener('mouseleave',function(){
      svg.classList.remove('hov');
      svg.querySelectorAll('.on').forEach(function(x){x.classList.remove('on')})})})});
"""


# Shorten text to n characters for a box label.
def _short(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# The look of an item box, from its page type.
def _kind(page_type: str) -> str:
    if page_type == "risk":
        return "risk"
    if "requirement" in page_type or page_type in ("feature", "user_story", "epic"):
        return "req"
    return "other"


# A readable name for a shared page that is only known by its slug (people/ravi-menon).
def _slug_title(slug: str) -> str:
    return slug.rsplit("/", 1)[-1].replace("-", " ").title()


# Build the SVG for one manifest. Returns (svg, counts) or None when there is nothing to draw.
def render(manifest: dict) -> tuple[str, dict] | None:
    """The section graph of a document, or None when the manifest has no sections or items."""
    pages = {p["slug"]: p for p in manifest.get("pages", [])}
    sections = [s for s in manifest.get("sections", []) if s.get("slug") in pages]
    items = [
        s for s, p in pages.items() if p["type"] not in NOT_ITEMS and not s.startswith("_")
    ]
    if not sections or not items:
        return None
    section_slugs = {s["slug"] for s in sections}
    # Which sections mention each target, and the typed links between items and shared pages.
    mentioned_in: dict[str, set[str]] = defaultdict(set)
    arcs: list[tuple[str, str, str]] = []
    for slug, page in pages.items():
        for verb, targets in page.get("links", {}).items():
            for target in targets:
                if slug in section_slugs and verb == "mentions_item":
                    mentioned_in[target].add(slug)
                elif verb not in STRUCTURAL_VERBS and slug in items:
                    arcs.append((slug, target, verb))
    shared = sorted({t for t in mentioned_in if t not in pages} | {t for _, t, _ in arcs if t not in pages})
    # The most connected items first, and only the most connected when there are too many.
    degree = defaultdict(int)
    for item in items:
        degree[item] += len(mentioned_in.get(item, ())) + sum(item in (a, b) for a, b, _ in arcs)
    items.sort(key=lambda s: (-degree[s], pages[s]["title"]))
    left_out = max(0, len(items) - MAX_ITEMS)
    items = sorted(items[:MAX_ITEMS], key=lambda s: (_kind(pages[s]["type"]), pages[s]["title"]))
    drawn_shared = [s for s in shared if any(s in (a, b) for a, b, _ in arcs) or mentioned_in.get(s)]

    pos: dict[str, tuple[str, int]] = {}
    for i, s in enumerate(sections):
        pos[s["slug"]] = ("section", 50 + i * ROW)
    for i, s in enumerate(items):
        pos[s] = ("item", 50 + i * ROW)
    for i, s in enumerate(drawn_shared):
        pos[s] = ("shared", 50 + i * ROW)
    height = 50 + max(len(sections), len(items), len(drawn_shared)) * ROW + 36
    verbs = sorted({v for a, b, v in arcs if a in pos and b in pos})
    color = {v: VERB_COLORS[i % len(VERB_COLORS)] for i, v in enumerate(verbs)}
    ids = {s: str(i) for i, s in enumerate(pos)}

    def edge_point(slug: str, side: str) -> tuple[int, int]:
        col, y = pos[slug]
        return COLUMN_X[col] + (COLUMN_W[col] if side == "r" else 0), y + 16

    out = [
        f'<svg class="gv" viewBox="0 0 {WIDTH} {height}" role="img" '
        f'aria-label="Section graph of this document">',
        '<text x="16" y="24" class="cap">SECTIONS</text>'
        '<text x="360" y="24" class="cap">ITEMS FOUND IN THEM</text>'
        '<text x="760" y="24" class="cap">SHARED ENTITIES</text>',
    ]
    mention_count = 0
    for target, secs in mentioned_in.items():
        if target not in pos:
            continue
        for sec in secs:
            (x1, y1), (x2, y2) = edge_point(sec, "r"), edge_point(target, "l")
            cx = (x1 + x2) / 2
            out.append(
                f'<path class="e{" multi" if len(secs) > 1 else ""}" data-a="{ids[sec]}" '
                f'data-b="{ids[target]}" d="M{x1},{y1} C{cx},{y1} {cx},{y2} {x2},{y2}"/>'
            )
            mention_count += 1
    for a, b, verb in arcs:
        if a not in pos or b not in pos:
            continue
        (x1, y1) = edge_point(a, "r")
        (x2, y2) = edge_point(b, "r" if pos[b][0] == "item" else "l")
        if pos[b][0] == "item":
            bend = 50 + 8 * (abs(y1 - y2) // ROW)
            d = f"M{x1},{y1} C{x1 + bend},{y1} {x2 + bend},{y2} {x2},{y2}"
        else:
            cx = (x1 + x2) / 2
            d = f"M{x1},{y1} C{cx},{y1} {cx},{y2} {x2},{y2}"
        out.append(
            f'<path class="e v" style="stroke:{color[verb]}" data-a="{ids[a]}" data-b="{ids[b]}" d="{d}"/>'
        )
        if pos[b][0] == "item":
            out.append(
                f'<text class="vl" x="{x1 + 52}" y="{(y1 + y2) / 2 + 4}" '
                f'style="fill:{color[verb]}">{html.escape(verb)}</text>'
            )
    for slug, (col, y) in pos.items():
        if col == "section":
            heading = next(s["heading"] for s in sections if s["slug"] == slug)
            title, sub, cls = _short(heading, 32), "", "sec"
        elif col == "shared":
            title, sub, cls = _short(_slug_title(slug), 28), "shared", "shared"
        else:
            page = pages[slug]
            n = len(mentioned_in.get(slug, ()))
            title = _short(page["title"], 40)
            sub = page["type"].replace("_", " ") + (f" · in {n} section{'s' if n != 1 else ''}" if n else "")
            cls = _kind(page["type"])
            if page.get("fields", {}).get("extracted_by") == "ai":
                sub += " · AI"
        multi = col == "item" and len(mentioned_in.get(slug, ())) > 1
        ai = col == "item" and pages[slug].get("fields", {}).get("extracted_by") == "ai"
        out.append(
            f'<g class="node {cls}{" multi" if multi else ""}{" ai" if ai else ""}" data-id="{ids[slug]}" '
            f'transform="translate({COLUMN_X[col]},{y})"><rect width="{COLUMN_W[col]}" height="34" rx="7"/>'
            f'<text x="9" y="14" class="t">{html.escape(title)}</text>'
            f'<text x="9" y="27" class="s">{html.escape(sub)}</text></g>'
        )
    legend = "".join(
        f'<tspan fill="{c}" font-weight="600"> — {html.escape(v)}</tspan>' for v, c in color.items()
    )
    out.append(
        f'<text x="16" y="{height - 12}" class="s">grey: section mentions item · bold outline: item in more '
        f'than one section · dashed: found by the AI{legend}</text></svg>'
    )
    counts = {
        "sections": len(sections),
        "items": len(items),
        "shared": len(drawn_shared),
        "links": mention_count + sum(1 for a, b, _ in arcs if a in pos and b in pos),
        "left_out": left_out,
    }
    return "".join(out), counts
