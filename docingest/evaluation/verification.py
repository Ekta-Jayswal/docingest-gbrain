# What this file does: the checks run after each ingest. It confirms that nothing was lost in
# conversion, that every page, chunk and link reached the brain, and that search finds the
# sections. Each check returns pass, warn, fail or info with a detail line.

"""Post-ingestion verification.

Checks, per document:
  conversion      no text lost converting the source file to markdown
  sections        section pages partition the markdown exactly (nothing dropped or duplicated)
  tables          every row of every ID-keyed table became an entity
  references      every ID referenced in the text is defined
  pages           every expected page is in the brain with the right type and title
  content         stored page text and attributes match what was written
  chunks          chunks cover every byte of every page's stored text
  links           every expected relationship edge exists
  search          each section is findable through the keyword index
and globally: shared entity pages, type coverage, embeddings, stale pages.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from docingest.utils import clock
from docingest.utils.gbrain_client import GBrain, Reader
from docingest.utils.markdown_parsing import normalize_ws, token_coverage, tokens

# The four statuses every check uses. FAIL is worst, then WARN, then PASS.
PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"
# The share of source words that must survive conversion. Below this a conversion fails. Above
# it, but with some words missing, it warns.
COVERAGE_MIN = 0.98
# The most items listed per check in the report. item_count still gives the true total.
MAX_ITEMS = 25


# Build one check result: its name, status, a detail line, and up to MAX_ITEMS items behind it.
def _check(name: str, status: str, detail: str, items: list | None = None) -> dict:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "items": (items or [])[:MAX_ITEMS],
        "item_count": len(items or []),
    }


# A document's (or the run's) status is the worst status among its checks.
def _status(checks: list[dict]) -> str:
    states = {c["status"] for c in checks}
    return FAIL if FAIL in states else WARN if WARN in states else PASS


# ---- document-level checks (no brain needed) ---------------------------------------


# Check 1: did the markdown keep every word of the source? OCR warnings fail at once, because OCR
# text is not reliable.
def check_conversion(m: dict, markdown: str, reference: str) -> dict:
    """Every word of the reference text must appear in the markdown."""
    if not markdown.strip():
        return _check("conversion", FAIL, "Converted markdown is empty")
    ocr = [w for w in m["warnings"] if "OCR" in w]
    if ocr:
        return _check("conversion", FAIL, ocr[0])
    coverage, missing = token_coverage(reference, markdown)
    # Every lost word is reported; small losses (PDF hyphenation, headers) warn, large ones fail.
    status = PASS if not missing else WARN if coverage >= COVERAGE_MIN else FAIL
    return _check(
        "conversion",
        status,
        f"{coverage:.2%} of source words present in markdown ({m['converter']})",
        [f"missing word: {t}" for t in missing],
    )


# Check 2: the sections must cover the markdown exactly, with each section page matching its slice.
def check_sections(m: dict, markdown: str) -> dict:
    """Section pages must partition the markdown exactly."""
    problems, pos = [], 0
    pages = {p["slug"]: p for p in m["pages"]}
    for s in m["sections"]:
        if s["start"] != pos:
            problems.append(
                f"gap or overlap before section {s['index'] + 1} ({pos}..{s['start']})"
            )
        pos = s["end"]
        body = pages.get(s["slug"], {}).get("body")
        if body != markdown[s["start"] : s["end"]]:
            problems.append(f"section page {s['slug']} text differs from the markdown slice")
    if pos != len(markdown):
        problems.append(f"sections end at {pos}, markdown has {len(markdown)} chars")
    joined = "".join(pages.get(s["slug"], {}).get("body", "") for s in m["sections"])
    if joined != markdown:
        problems.append("concatenated section pages do not reproduce the markdown")
    return _check(
        "sections",
        FAIL if problems else PASS,
        f"{len(m['sections'])} sections cover {len(markdown)} chars exactly"
        if not problems
        else "Section partition is not exact",
        problems,
    )


# Check 3: every ID row of an entity table must have produced an entity. Tables with no ID
# column are only noted, not failed.
def check_tables(m: dict) -> dict:
    """Every row of every ID-keyed table must have produced an entity."""
    items, missing = [], []
    ids = m["entity_ids"]
    for t in m["tables"]:
        items.append(
            f"section {t['section'] + 1}, {len(t['rows'])} rows, "
            f"columns {t['header'][:4]}: {t['mapped_to']}"
        )
        if t["mapped_to"].startswith("entities"):
            id_col = t.get("id_col")
            for row in t["rows"]:
                item = (row.get(id_col) or "").strip() if id_col else ""
                if item and item not in ids:
                    missing.append(f"row {item} has no entity")
    text_only = sum(1 for t in m["tables"] if t["mapped_to"] == "text only")
    if missing:
        return _check("tables", FAIL, f"{len(missing)} ID rows produced no entity", missing)
    detail = f"{len(m['tables'])} tables; every ID row became an entity"
    if text_only:
        detail += f"; {text_only} table(s) kept as section text only (no ID column)"
    return _check("tables", PASS if not text_only else INFO, detail, items)


# Check 4: an ID mentioned in the text but never defined in the document is a warning.
def check_references(m: dict) -> dict:
    """Every ID mentioned in the text should be defined in the document."""
    refs = m["undefined_refs"]
    if refs:
        return _check(
            "references",
            WARN,
            f"{len(refs)} referenced ID(s) are not defined in the document",
            refs,
        )
    if not m["entity_ids"]:
        return _check("references", PASS, "No IDs defined or referenced")
    return _check(
        "references",
        PASS,
        f"All {len(m['entity_ids'])} defined IDs resolve; no dangling references",
    )


# Check 5: every fallback to a generic record or link is reported. Those still waiting for a
# reviewer are warnings.
def check_schema_gaps(m: dict) -> dict:
    """Fallbacks to `record` or generic `references` links must be reported, not silent."""
    gaps = m.get("gaps") or []
    if not gaps:
        return _check("schema gaps", PASS, "No schema fallbacks: every ID prefix and link column is known")
    open_ = [g for g in gaps if g.get("status") != "unmapped" and g["kind"] != "verb_mismatch"]
    items = [
        f"{g['kind'].replace('_', ' ')} {g['key'].split(':', 1)[1]} ({g['rows']} row(s), section "
        f"{g['section'] + 1}): "
        + (g["proposal"] + " waits for a reviewer" if g.get("proposal") else g.get("status", "reported only"))
        for g in gaps
    ]
    if open_:
        return _check(
            "schema gaps",
            WARN,
            f"{len(open_)} fallback(s) to record or generic links; stored safely, "
            "proposals wait for a reviewer (./ingest.sh review list)",
            items,
        )
    return _check("schema gaps", INFO, f"{len(gaps)} fallback(s) kept generic on purpose", items)


# ---- brain checks -------------------------------------------------------------------


# Check 6: low-confidence sections must have been sent to the AI. Without --llm they are
# reported as information.
def check_routing(m: dict) -> dict:
    """Low-confidence sections must have been read by the AI, or be reported as not read."""
    routing = m.get("routing") or []
    low = [d for d in routing if d["route"] == "ai"]
    items = [f"section {d['section']} ({d['heading']}): {'; '.join(d['reasons'])}" for d in low]
    if not low:
        return _check(
            "rule confidence",
            PASS,
            f"All {len(routing)} sections fully covered by rule extraction",
        )
    # The AI report, if the document was extracted with --llm.
    lr = m.get("llm_report") or {}
    if not lr:
        return _check(
            "rule confidence",
            INFO,
            f"{len(low)} of {len(routing)} sections hold content rules cannot extract "
            "(stored, chunked and searchable, but entities in it are not extracted); "
            "run with --llm to extract them",
            items,
        )
    sent = set(lr.get("sent", []))
    failed = {
        int(e.split(":")[0].split()[1]) for e in lr.get("errors", []) if e.startswith("section ")
    }
    missed = [d for d in low if d["section"] not in sent or d["section"] in failed]
    if missed:
        return _check(
            "rule confidence",
            WARN,
            f"{len(missed)} low-confidence section(s) were not extracted by the AI "
            "(call failed or skipped)",
            [f"section {d['section']} ({d['heading']})" for d in missed],
        )
    return _check(
        "rule confidence",
        PASS,
        f"{len(routing) - len(low)} sections by rules, {len(low)} low-confidence sections by AI "
        f"({lr.get('mode', 'all')} mode)",
        items,
    )


# gbrain keeps a page's text in two parts: the compiled truth and the timeline. Returns both.
def _stored_text(page: dict) -> tuple[str, str]:
    return (page.get("compiled_truth") or "", page.get("timeline") or "")


# Checks 7 and 8: every page exists in the brain with the right type and title, and its stored
# text and attributes match what was written.
def check_pages_in_brain(pages: list[dict], reader: Reader, cache: dict) -> list[dict]:
    """Every page must exist in the brain with the expected type, text and attributes."""
    missing, wrong_type, content, fields = [], [], [], []
    for p in pages:
        stored = reader.page(p["slug"])
        cache[p["slug"]] = stored
        if stored is None:
            missing.append(p["slug"])
            continue
        if stored.get("type") != p["type"]:
            wrong_type.append(f"{p['slug']}: stored {stored.get('type')}, expected {p['type']}")
        if stored.get("title") != p["title"]:
            content.append(f"{p['slug']}: title differs")
        ct, tl = _stored_text(stored)
        # The text matches when it is identical after whitespace is normalised, or when it has the
        # same words in any order (gbrain may re-space or reorder text).
        if normalize_ws(ct + " " + tl) != normalize_ws(p["body"]) and Counter(
            tokens(ct + " " + tl)
        ) != Counter(tokens(p["body"])):
            content.append(f"{p['slug']}: stored text differs from written text")
        # Attributes are checked against the page's frontmatter (the header block gbrain stores).
        fm = stored.get("frontmatter") or {}
        for k, v in p["fields"].items():
            if v in (None, "", []):
                continue
            # An attribute matches when the JSON forms are equal, or when the plain text forms are
            # equal (for example a number stored as text).
            if json.dumps(fm.get(k), sort_keys=True, default=str) != json.dumps(
                v, sort_keys=True, default=str
            ) and str(fm.get(k)) != str(v):
                fields.append(f"{p['slug']}: attribute {k} = {fm.get(k)!r}, expected {v!r}")
    out = [
        _check(
            "pages",
            FAIL if missing or wrong_type else PASS,
            f"{len(pages) - len(missing)}/{len(pages)} pages present with expected types"
            if not (missing or wrong_type)
            else f"{len(missing)} missing, {len(wrong_type)} wrong type",
            [f"missing: {s}" for s in missing] + wrong_type,
        ),
        _check(
            "content",
            FAIL if content or fields else PASS,
            "Stored text and attributes match what was written"
            if not (content or fields)
            else f"{len(content)} text and {len(fields)} attribute mismatches",
            content + fields,
        ),
    ]
    return out


# Mark every byte that a chunk covers, then report the stretches no chunk covers. Also counts
# chunks whose text does not match their stated byte range.
def _coverage_gaps(text: str, chunks: list[dict]) -> tuple[list[str], int]:
    """Uncovered non-whitespace spans of text, using chunk byte offsets (or text search)."""
    data = text.encode("utf-8")
    # One flag per byte: 1 when some chunk covers it.
    covered = bytearray(len(data))
    mismatched = 0
    for c in chunks:
        start, end = c.get("projected_byte_start"), c.get("projected_byte_end")
        ctext = c.get("chunk_text") or ""
        # No byte range from gbrain: find the chunk's text in the page instead.
        if start is None or end is None:
            idx = text.find(ctext.strip()[:200])
            if idx < 0:
                mismatched += 1
                continue
            start = len(text[:idx].encode("utf-8"))
            end = start + len(ctext.strip().encode("utf-8"))
        # The chunk's text must match the bytes it claims to cover, ignoring whitespace.
        elif normalize_ws(data[start:end].decode("utf-8", "ignore")) not in normalize_ws(
            ctext
        ) and normalize_ws(ctext) not in normalize_ws(data[start:end].decode("utf-8", "ignore")):
            mismatched += 1
        for i in range(max(start, 0), min(end, len(data))):
            covered[i] = 1
    gaps, i = [], 0
    while i < len(data):
        if not covered[i]:
            j = i
            while j < len(data) and not covered[j]:
                j += 1
            span = data[i:j].decode("utf-8", "ignore")
            # Blank gaps are fine. Only uncovered text is a problem.
            if span.strip():
                gaps.append(f"bytes {i}-{j}: {span.strip()[:60]!r}")
            i = j
        else:
            i += 1
    return gaps, mismatched


# Check 9: chunks must cover every byte of every page. Also returns the chunk count and how many
# chunks are embedded.
def check_chunks(pages: list[dict], reader: Reader, cache: dict) -> tuple[dict, int, int]:
    """Chunks must cover every byte of every page.

    Returns the check, the chunk count and the number of embedded chunks.
    """
    problems, total, embedded = [], 0, 0
    for p in pages:
        stored = cache.get(p["slug"])
        if stored is None:
            continue
        chunks = reader.chunks(p["slug"])
        total += len(chunks)
        # A chunk counts as embedded when it has an embedding time, or when its null flag is false.
        embedded += sum(
            1 for c in chunks if c.get("embedded_at") or c.get("embedding_is_null") is False
        )
        ct, tl = _stored_text(stored)
        if (ct.strip() or tl.strip()) and not chunks:
            problems.append(f"{p['slug']}: no chunks")
            continue
        for source, text in (("compiled_truth", ct), ("timeline", tl)):
            if not text.strip():
                continue
            # Each text part is checked against only the chunks that came from it.
            subset = [c for c in chunks if (c.get("chunk_source") or "compiled_truth") == source]
            gaps, mismatched = _coverage_gaps(text, subset)
            problems += [f"{p['slug']} ({source}) uncovered {g}" for g in gaps]
            if mismatched:
                problems.append(
                    f"{p['slug']} ({source}): {mismatched} chunk(s) do not match their byte range"
                )
    return (
        _check(
            "chunks",
            FAIL if problems else PASS,
            f"{total} chunks cover every byte of {len(pages)} pages"
            if not problems
            else f"{len(problems)} coverage problems across {total} chunks",
            problems,
        ),
        total,
        embedded,
    )


# Check 10: every link the pipeline wrote must exist in the brain. Extra links are warned about.
def check_links(pages: list[dict], reader: Reader) -> tuple[dict, int]:
    """Every expected edge must exist. Returns the check and the number of edges found."""
    missing, extra, total = [], [], 0
    for p in pages:
        # The links the pipeline expects, as (verb, target) pairs.
        expected = {(verb, t) for verb, targets in p["links"].items() for t in targets}
        actual = {
            (link["link_type"], link["to_slug"])
            for link in reader.links(p["slug"])
            if link.get("from_slug") == p["slug"]
        }
        total += len(expected & actual)
        missing += [f"{p['slug']} --{v}--> {t}" for v, t in sorted(expected - actual)]
        extra += [f"{p['slug']} --{v}--> {t}" for v, t in sorted(actual - expected)]
    expected_total = total + len(missing)
    status = FAIL if missing else WARN if extra else PASS
    detail = f"{total}/{expected_total} expected edges present"
    if extra:
        detail += f"; {len(extra)} unexpected edge(s)"
    return _check(
        "links",
        status,
        detail,
        [f"missing: {m}" for m in missing] + [f"unexpected: {e}" for e in extra],
    ), total


# Check 11: each section must be findable by a word that appears only in that section.
# Short words and words with digits or symbols are not used.
def check_search(m: dict, markdown: str, reader: Reader, limit: int) -> dict:
    """Each section must be findable by a word that occurs only in that section."""
    # The distinct words of each section. Counts then show which words appear in only one section.
    section_tokens = [(s, set(tokens(markdown[s["start"] : s["end"]]))) for s in m["sections"]]
    counts = Counter(t for _, ts in section_tokens for t in ts)
    misses, tested = [], 0
    # Test each section (or the first `limit` sections, when a limit is given; 0 means all).
    for s, ts in section_tokens[:limit] if limit else section_tokens:
        unique = sorted(
            (t for t in ts if counts[t] == 1 and len(t) >= 5 and t.isalpha()),
            key=len,
            reverse=True,
        )
        if not unique:
            continue
        tested += 1
        word = unique[0]
        hits = reader.search(word, types=[m["section_type"]])
        if s["slug"] not in {h.get("slug") for h in hits}:
            misses.append(f"'{word}' did not find {s['slug']}")
    if not tested:
        return _check("search", INFO, "No section has a word unique to it; search check skipped")
    return _check(
        "search",
        FAIL if misses else PASS,
        f"{tested - len(misses)}/{tested} sections found by keyword search",
        misses,
    )


# ---- orchestration --------------------------------------------------------------------


# Run every check for each listed document, then the shared pages and the brain-wide checks.
# Returns the full report.
def verify(work: Path, gb: GBrain, keys: list[str], search_limit: int = 0) -> dict:
    """Run every check for the given documents and the shared pages; return the report."""
    started = time.time()
    report = {
        "run_at": clock.iso(),
        "brain_home": str(gb.home),
        "documents": [],
        "global": {},
    }
    totals = Counter()
    with Reader(gb) as reader:
        for key in keys:
            mpath = work / "manifests" / f"{key}.json"
            if not mpath.exists():
                report["documents"].append(
                    {
                        "key": key,
                        "status": FAIL,
                        "checks": [
                            _check(
                                "manifest",
                                FAIL,
                                f"No manifest at {mpath}; was the document ingested?",
                            )
                        ],
                    }
                )
                continue
            m = json.loads(mpath.read_text())
            markdown = (work / "markdown" / f"{key}.md").read_text()
            reference = (work / "reference" / f"{key}.txt").read_text()
            cache: dict = {}
            checks = [
                check_conversion(m, markdown, reference),
                check_sections(m, markdown),
                check_tables(m),
                check_references(m),
            ]
            checks += check_pages_in_brain(m["pages"], reader, cache)
            chunk_check, n_chunks, n_emb = check_chunks(m["pages"], reader, cache)
            link_check, n_links = check_links(m["pages"], reader)
            checks += [chunk_check, link_check, check_search(m, markdown, reader, search_limit)]
            checks.append(check_routing(m))
            checks.append(check_schema_gaps(m))
            # AI results are checked too, when the document was extracted with --llm.
            if m.get("llm_report"):
                lr = m["llm_report"]
                checks.append(
                    _check(
                        "ai extraction",
                        WARN if lr.get("rejected") else PASS,
                        f"{lr.get('accepted', 0)} AI items accepted, "
                        f"{lr.get('rejected', 0)} rejected "
                        "(rejected items had no verbatim evidence or invalid endpoints)",
                        lr.get("rejections", []),
                    )
                )
            totals.update(pages=len(m["pages"]), chunks=n_chunks, links=n_links, embedded=n_emb)
            report["documents"].append(
                {
                    "key": key,
                    "source": m["source_path"],
                    "kind": m["kind"],
                    "title": m["title"],
                    "status": _status(checks),
                    "pages": len(m["pages"]),
                    "chunks": n_chunks,
                    "links": n_links,
                    "warnings": m["warnings"],
                    "checks": checks,
                }
            )

        # Shared entity pages are verified once per run, from their own manifest.
        shared_path = work / "manifests" / "_shared.json"
        g_checks = []
        if shared_path.exists():
            shared = json.loads(shared_path.read_text())
            cache = {}
            g_checks += check_pages_in_brain(shared["pages"], reader, cache)
            chunk_check, n_chunks, n_emb = check_chunks(shared["pages"], reader, cache)
            link_check, n_links = check_links(shared["pages"], reader)
            g_checks += [chunk_check, link_check]
            totals.update(
                pages=len(shared["pages"]), chunks=n_chunks, links=n_links, embedded=n_emb
            )
            for c in g_checks:
                c["name"] = f"shared {c['name']}"

    # Brain-wide checks: every page should have a type, and no page should use an undeclared type.
    stats = gb.schema_stats().get("aggregate", {})
    untyped, undeclared = stats.get("untyped_pages", 0), stats.get("undeclared_pages", 0)
    g_checks.append(
        _check(
            "type coverage",
            FAIL if untyped or undeclared else PASS,
            f"{stats.get('typed_pages')}/{stats.get('total_pages')} pages typed; "
            f"{untyped} untyped, {undeclared} with undeclared types",
        )
    )
    g_checks.append(
        _check(
            "embeddings",
            INFO,
            f"{totals['embedded']} of {totals['chunks']} verified chunks "
            "embedded (ingestion runs with --no-embed)",
        )
    )
    g_checks.append(stale_pages_check(work, gb))
    g_checks.append(schema_review_check(work))

    # The run's overall status is the worst of the shared checks and of every document.
    status = _status([*g_checks, *[{"status": d["status"]} for d in report["documents"]]])
    report["global"] = {"status": _status(g_checks), "checks": g_checks}
    report["summary"] = {
        "status": status,
        "documents": len(report["documents"]),
        "documents_failed": sum(1 for d in report["documents"] if d["status"] == FAIL),
        "pages": totals["pages"],
        "chunks": totals["chunks"],
        "links": totals["links"],
        "seconds": round(time.time() - started, 1),
    }
    return report


# Report the schema proposals still waiting for a reviewer, and the decisions made in the last
# week.
def schema_review_check(work: Path) -> dict:
    """Open proposals, and decisions applied in the last week with who made them."""
    from docingest.ingestion.review_proposals import RECENT_DAYS, ReviewBoard

    board = ReviewBoard(work)
    open_, recent = sorted(board.pending()), board.recent()
    items = [f"{p} waits for a reviewer" for p in open_] + [
        f"{e['at'][:10]} {e['event']} {e['proposal']} by {e.get('by', '?')}" for e in recent
    ]
    return _check(
        "schema review",
        INFO,
        f"{len(open_)} open proposal(s); {len(recent)} decision(s) applied or undone in the last "
        f"{RECENT_DAYS} days",
        items,
    )


# Check 12: pages under the pipeline's folders that no current manifest produces are left over
# from older runs.
def stale_pages_check(work: Path, gb: GBrain) -> dict:
    """No page under the pipeline's folders may remain that no manifest produces."""
    expected = set()
    for mpath in (work / "manifests").glob("*.json"):
        expected |= {p["slug"] for p in json.loads(mpath.read_text())["pages"]}
    listed = gb.run("list", "--limit", "1000000").stdout.splitlines()
    # Only pages in these folders belong to the pipeline. Other pages in the brain are left alone.
    ours = ("docs/", "people/", "companies/")
    present = {line.split("\t")[0] for line in listed if line.startswith(ours)}
    stale = sorted(present - expected)
    return _check(
        "stale pages",
        WARN if stale else PASS,
        f"{len(stale)} page(s) in the brain under docs/, people/ or companies/ that no ingested "
        "document produces"
        if stale
        else "No stale pages from earlier runs",
        stale,
    )


# Escape pipes and line breaks so the text stays inside one table cell.
def _cell(text: str) -> str:
    """Make text safe inside a markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


# Write the verification report as markdown. Each check is a row, and the items behind any
# warning or failure are listed below the table.
def render_markdown(report: dict) -> str:
    """Render a verification report as markdown."""
    s = report["summary"]
    icon = {PASS: "PASS", WARN: "WARN", FAIL: "FAIL", INFO: "INFO"}
    out = [
        f"# Ingestion verification — {icon[s['status']]}",
        "",
        f"- Run: {report['run_at']}",
        f"- Brain: {report['brain_home']}",
        f"- Documents: {s['documents']} ({s['documents_failed']} failed)",
        f"- Pages: {s['pages']}, chunks: {s['chunks']}, links: {s['links']}",
        f"- Took {s['seconds']} s",
        "",
    ]
    for d in report["documents"]:
        out += [
            f"## {icon[d['status']]} {d.get('title', d['key'])}",
            "",
            f"`{d.get('source', '')}` · kind **{d.get('kind', '?')}** · key `{d['key']}` · "
            f"{d.get('pages', 0)} pages, {d.get('chunks', 0)} chunks, {d.get('links', 0)} links",
            "",
            "| Check | Result | Detail |",
            "|---|---|---|",
        ]
        out += [
            f"| {c['name']} | {icon[c['status']]} | {_cell(c['detail'])} |" for c in d["checks"]
        ]
        if d.get("warnings"):
            out += ["", "**Warnings:**", ""] + [f"- {w}" for w in d["warnings"]]
        for c in d["checks"]:
            if c["items"] and (c["status"] in (FAIL, WARN) or c["name"] == "rule confidence"):
                out += ["", f"**{c['name']}** ({c['item_count']}):", ""]
                out += [f"- {i}" for i in c["items"]]
        out.append("")
    out += ["## Global", "", "| Check | Result | Detail |", "|---|---|---|"]
    out += [
        f"| {c['name']} | {icon[c['status']]} | {_cell(c['detail'])} |"
        for c in report["global"]["checks"]
    ]
    for c in report["global"]["checks"]:
        if c["items"] and (c["status"] in (FAIL, WARN) or c["name"] == "rule confidence"):
            out += ["", f"**{c['name']}** ({c['item_count']}):", ""]
            out += [f"- {i}" for i in c["items"]]
    return "\n".join(out) + "\n"
