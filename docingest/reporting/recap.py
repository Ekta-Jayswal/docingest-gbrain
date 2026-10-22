# What this file does: writes the summary printed at the end of an ingest run.
# It is built only from saved records (manifests, the verification report and the evaluation
# history), so it always matches the run history.

"""The end-of-run recap: what each document became, its checks and its latest evaluation.

``lines`` only builds text; ``commands.cmd_ingest`` prints it as the last block of a run.
Everything it reports comes from the manifests, the verification report and the
evaluation history, so it is the same record the run history keeps.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from docingest.evaluation.verification import FAIL, INFO, PASS, WARN
from docingest.reporting import run_history


# Format a 0-to-1 score as a percentage, or "n/a" when there is no score.
def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


# Find the newest evaluation recorded for a document with this file name.
def _latest_eval(work: Path, source: str) -> dict | None:
    """The newest evaluation of a document with this file name (``docingest eval``), if any."""
    name = Path(source).name
    rows = [r for r in run_history.read(work, "evals") if Path(r["document"]).name == name]
    return max(rows, key=lambda r: r["evaluated_at"], default=None)


# Format one evaluation's scores on a single line; "this run" or the date it was recorded.
def _eval_line(ev: dict, when: str) -> str:
    s = ev["summary"]
    parts = [
        f"overall {_pct(s['overall'])}",
        f"classification {_pct(s['classification'])}",
        f"sections F1 {_pct(s['sections_f1'])}",
        f"tables {_pct(s['tables_accuracy'])}",
        f"entities F1 {_pct(s['entities_f1'])}",
        f"attributes {_pct(s['attributes_accuracy'])}",
        f"links F1 {_pct(s['links_f1'])}",
        f"storage {_pct(s['storage_pass_rate'])}",
        f"retrieval {_pct(s['retrieval_hit_rate'])}",
    ]
    return f"  eval ({when}): " + ", ".join(parts)


# Build the recap lines for one document: its content, chunks, links, rule and AI results,
# test status, evaluation and time taken.
def _document(
    work: Path,
    key: str,
    verified: dict | None,
    seconds: float | None,
    parent: str | None,
    evals: list[dict],
) -> list[str]:
    # Build the same record the run history saves, so the recap and the history agree.
    rec = run_history.document_record(work, key, verified, seconds, parent)
    manifest = json.loads((work / "manifests" / f"{key}.json").read_text())
    name = Path(rec["source_path"]).name
    title = f"{name} ({rec['kind']})" + (f", attachment of {parent}" if parent else "")
    lines = [title]

    content = f"  content: {len(rec['sections'])} sections, {len(rec['tables'])} tables, {rec['pages']} pages"
    if rec["entities"]:
        content += f", {rec['entities']} entities"
    lines.append(content)

    # No chunk count means verification was skipped, so say that rather than show a number.
    chunks = "not counted (verification skipped)" if rec["chunks"] is None else str(rec["chunks"])
    links = f"{rec['links']}"
    # Show the four most common link types, most frequent first.
    top = sorted(rec["links_by_verb"].items(), key=lambda item: (-item[1], item[0]))[:4]
    if top:
        links += " (" + ", ".join(f"{v} {n}" for v, n in top) + (", ..." if len(rec["links_by_verb"]) > 4 else "") + ")"
    lines.append(f"  chunks: {chunks}    links: {links}")

    rules = next((c["detail"] for c in rec["checks"] if c["name"] == "rule confidence"), None)
    if rules:
        lines.append(f"  rules: {rules}")
    llm = manifest.get("llm_report")
    if llm:
        lines.append(
            f"  AI: {llm.get('accepted', 0)} accepted, {llm.get('rejected', 0)} rejected "
            f"(~${llm.get('estimated_cost_usd', 0)})"
        )

    if not rec["checks"]:
        lines.append("  tests: not run (--no-verify)")
    else:
        counts = Counter(c["status"] for c in rec["checks"])
        lines.append(
            f"  tests: {rec['status'].upper()} ({counts[PASS]} pass, {counts[WARN]} warn, "
            f"{counts[FAIL]} fail)"
        )
        for c in rec["checks"]:
            if c["status"] in (WARN, FAIL):
                lines.append(f"    {c['status'].upper()} {c['name']}: {c['detail']}")

    # Prefer this run's evaluation of the document; otherwise the newest one on record.
    ran = next((e for e in evals if Path(e["document"]).name == name), None)
    if ran:
        lines.append(_eval_line(ran, "this run"))
    elif ev := _latest_eval(work, rec["source_path"]):
        lines.append(_eval_line(ev, ev["evaluated_at"][:10]))
    if rec["seconds"] is not None:
        lines.append(f"  time: {rec['seconds']} s")
    return lines


# Build the full recap for one run: one block per document, then the run totals.
def lines(
    work: Path,
    result: dict,
    report: dict | None,
    embed_report: dict | None,
    run_id: str,
    evals: list[dict] | None = None,
) -> list[str]:
    """The recap of one ingestion run, one block per document, then the run totals.

    ``evals`` are the evaluations this run made (None when evaluation was skipped).
    """
    # Index the verification results by document key, so each document finds its own.
    verified = {d["key"]: d for d in (report or {}).get("documents", [])}
    out = [f"Summary of run {run_id}"]
    for key in result["processed"]:
        out += _document(
            work,
            key,
            verified.get(key),
            result["timings"].get(key),
            result["parents"].get(key),
            evals or [],
        )

    out.append(f"Totals: {len(result['processed'])} document(s) ingested, {len(result['failed'])} failed")
    # An evaluation ran, but no answer key matched these documents. Say so, rather than show
    # nothing.
    if evals == [] and result["processed"]:
        out.append("  eval: no answer key matches these documents")
    if report:
        s = report["summary"]
        out.append(
            f"  tests: {s['status'].upper()} ({s['pages']} pages, {s['chunks']} chunks, "
            f"{s['links']} links verified)"
        )
    if embed_report:
        out.append(f"  embeddings: {embed_report['status'].upper()}")
    if result["deleted"]:
        out.append(f"  deleted: {len(result['deleted'])} page(s) no longer produced by any document")
    for f in result["failed"]:
        out.append(f"  FAILED {Path(f['file']).name}: {f['error']}")
    if result["proposals_applied"]:
        out.append(f"  schema: the rules applied {', '.join(result['proposals_applied'])}")
    if result["proposals_rolled_back"]:
        out.append(f"  schema: rolled back {', '.join(result['proposals_rolled_back'])} (verification failed)")
    # Schema proposals that need a human decision are listed, with the command to review them.
    if result["proposals_pending"]:
        out.append(
            f"  schema: {len(result['proposals_pending'])} proposal(s) wait for a reviewer: "
            f"{', '.join(result['proposals_pending'])} (./ingest.sh review list)"
        )
    return out
