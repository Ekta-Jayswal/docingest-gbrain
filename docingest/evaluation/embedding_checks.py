# What this file does: checks the embeddings (the meaning vectors used for search) after an
# ingest. It confirms every chunk has a vector, that gbrain agrees, and that searches find
# the right pages.

"""Embedding verification: are all chunks embedded, and does semantic search work?"""

from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from docingest.utils.gbrain_client import GBrain, GBrainError, Reader

# Questions worded unlike the documents, each with the page it should find.
DEFAULT_QUERIES = Path(__file__).with_name("semantic_search_questions.yaml")


# Build one check result: its name, status (pass, warn, fail or info), detail and any items.
def _check(name: str, status: str, detail: str, items: list | None = None) -> dict:
    return {"name": name, "status": status, "detail": detail, "items": items or []}


# Pick up to n items spread evenly through the list. The same input gives the same sample.
def _sample(items: list, n: int) -> list:
    """An evenly spaced, repeatable sample of at most ``n`` items."""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# Check that every chunk of every page has a vector from the expected model. Also returns
# the first chunk's text of each page, for the search checks.
def check_coverage(reader: Reader, slugs: list[str], model: str) -> tuple[dict, dict[str, str]]:
    """Every live chunk has a vector from the expected model. Returns the check and page texts."""
    missing, wrong_model, total = [], [], 0
    first_chunk: dict[str, str] = {}
    for slug in slugs:
        for c in reader.chunks(slug):
            total += 1
            # Keep the text of each page's first chunk. Later checks use it as a search query.
            first_chunk.setdefault(slug, c.get("chunk_text", ""))
            # A chunk with no vector is missing. A chunk whose vector came from another model is
            # wrong.
            if c.get("embedding_is_null") or not c.get("embedded_at"):
                missing.append(f"{slug} chunk {c.get('chunk_index')}")
            elif c.get("model") != model:
                wrong_model.append(f"{slug} chunk {c.get('chunk_index')}: {c.get('model')}")
    if missing or wrong_model:
        return (
            _check(
                "coverage",
                "fail",
                f"{len(missing)} of {total} chunks have no vector, {len(wrong_model)} come from "
                f"another model than {model}",
                (missing + wrong_model)[:20],
            ),
            first_chunk,
        )
    return _check("coverage", "pass", f"All {total} chunks have a {model} vector"), first_chunk


# Ask gbrain's own health check whether embeddings are on, nothing is pending, and the model
# and width match the expected ones.
def check_provider(gb: GBrain, model: str, dimensions: int) -> dict:
    """gbrain's own health check agrees: embeddings on, nothing pending."""
    try:
        st = gb.embedding_state()
    except GBrainError as e:
        return _check("provider health", "fail", f"gbrain doctor failed: {e}")
    counts = st.get("counts", {})
    cfg = gb.config()
    problems = []
    if not st.get("embeddings_enabled", False):
        problems.append("embeddings are not enabled")
    if counts.get("pending"):
        problems.append(f"{counts['pending']} chunk(s) still pending")
    if counts.get("quarantined"):
        problems.append(f"{counts['quarantined']} chunk(s) quarantined")
    # A brain built with a different model would search with the wrong vectors, so it fails.
    if cfg.get("embedding_model") not in (None, model):
        problems.append(f"brain uses {cfg.get('embedding_model')}, expected {model}")
    if cfg.get("embedding_dimensions") not in (None, dimensions):
        problems.append(f"brain width is {cfg.get('embedding_dimensions')}, expected {dimensions}")
    if problems:
        return _check("provider health", "fail", "; ".join(problems), [st.get("message", "")])
    return _check("provider health", "pass", st.get("message", "")[:200])


# Search with a page's own text and check that the page comes back in the top 3 results.
def check_self_retrieval(reader: Reader, first_chunk: dict[str, str], sample: int) -> dict:
    """A query made of a chunk's own text finds that chunk's page among the top 3."""
    # Only pages with at least 80 characters of text are tested, since short text searches poorly.
    pages = [s for s, t in first_chunk.items() if len(t) >= 80]
    picked = _sample(sorted(pages), sample)
    if not picked:
        return _check("self-retrieval", "info", "No chunks long enough to test")
    misses = []
    for slug in picked:
        results = reader.query(first_chunk[slug][:300], limit=3)
        top = [r.get("slug") for r in results]
        # A copy of the same text under another slug (a duplicate document) also counts.
        same_text = any(first_chunk[slug][:100] in r.get("chunk_text", "") for r in results)
        if slug not in top and not same_text:
            misses.append(f"{slug} not in top 3 (got {top[:3]})")
    # Pass at 90% or more found, warn at 70% or more, otherwise fail.
    rate = 1 - len(misses) / len(picked)
    status = "pass" if rate >= 0.9 else "warn" if rate >= 0.7 else "fail"
    return _check(
        "self-retrieval", status, f"{len(picked) - len(misses)}/{len(picked)} pages found", misses
    )


# Run the wording-varied questions and check that each finds its page. Also counts how many
# plain keyword search finds, to show what meaning search adds.
def check_semantic(reader: Reader, queries_file: Path | None) -> dict:
    """Queries worded unlike the documents still find the right page (hybrid search)."""
    path = queries_file or DEFAULT_QUERIES
    try:
        queries = (yaml.safe_load(path.read_text()) or {}).get("queries", [])
    except OSError:
        queries = []
    if not queries:
        return _check("semantic search", "info", f"No semantic queries configured ({path})")
    misses, kw_hits = [], 0
    for q in queries:
        # Each question names the page slug (or part of it) it should find.
        expect = q["expect"]
        hits = [r.get("slug", "") for r in reader.query(q["query"], limit=5)]
        if not any(expect in h for h in hits):
            misses.append(f"“{q['query']}” expected …{expect}, got {hits[:3]}")
        # Count the questions that keyword search also finds.
        if any(expect in r.get("slug", "") for r in reader.search(q["query"], limit=5)):
            kw_hits += 1
    n = len(queries)
    # Pass with no misses, warn with up to a third missed, fail with more.
    status = "pass" if not misses else "warn" if len(misses) <= n // 3 else "fail"
    return _check(
        "semantic search",
        status,
        f"{n - len(misses)}/{n} queries found their page; "
        f"keyword search alone found {kw_hits}/{n}",
        misses,
    )


# Check that gbrain's combined keyword and meaning search returns scored results.
def check_hybrid(reader: Reader, first_chunk: dict[str, str]) -> dict:
    """gbrain's hybrid query returns scored results."""
    text = next((t for t in first_chunk.values() if len(t) >= 80), None)
    if text is None:
        return _check("hybrid query", "info", "No text to query with")
    results = reader.query(text[:200], limit=5)
    # A hybrid result must carry a numeric score. Results without one do not count.
    scored = [r for r in results if isinstance(r.get("score"), int | float)]
    if not scored:
        return _check("hybrid query", "fail", "gbrain query returned no scored results")
    return _check(
        "hybrid query", "pass", f"{len(scored)} scored result(s); top {scored[0]['score']}"
    )


# Run every embedding check and write the report to work/reports/ as JSON and markdown.
# Returns the report.
def run(
    gb: GBrain,
    work: Path,
    slugs: list[str],
    model: str,
    dimensions: int,
    queries_file: Path | None = None,
    sample: int = 20,
) -> dict:
    """Run all embedding checks and write ``work/reports/embeddings-latest.{md,json}``."""
    started = time.time()
    checks = [check_provider(gb, model, dimensions)]
    with Reader(gb) as reader:
        coverage, first_chunk = check_coverage(reader, slugs, model)
        checks.insert(0, coverage)
        checks += [
            check_self_retrieval(reader, first_chunk, sample),
            check_semantic(reader, queries_file),
            check_hybrid(reader, first_chunk),
        ]
    statuses = {c["status"] for c in checks}
    status = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    report = {
        "status": status,
        "model": model,
        "dimensions": dimensions,
        "pages": len(slugs),
        "seconds": round(time.time() - started, 1),
        "checks": checks,
    }
    reports = work / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "embeddings-latest.json").write_text(json.dumps(report, indent=1))
    (reports / "embeddings-latest.md").write_text(render(report))
    return report


# Turn the embedding report into markdown: a summary table, then the items behind any warning.
def render(report: dict) -> str:
    """The embedding report as markdown."""
    lines = [
        f"# Embedding verification — {report['status'].upper()}",
        "",
        f"- Model: {report['model']} ({report['dimensions']} dimensions)",
        f"- Pages checked: {report['pages']}, took {report['seconds']} s",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for c in report["checks"]:
        lines.append(f"| {c['name']} | {c['status'].upper()} | {c['detail']} |")
    for c in report["checks"]:
        if c["items"]:
            lines += ["", f"**{c['name']}**:", ""] + [f"- {i}" for i in c["items"]]
    return "\n".join(lines) + "\n"
