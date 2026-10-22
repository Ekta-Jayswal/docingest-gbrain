# What this file does: builds the run-history web page (one static HTML file) from the
# run history. It shows totals, activity, evaluation scores, runs and every document's details.

"""The run-history page: one static HTML file built from the run history.

Overall totals (by project, kind, file type, status, team), activity over time, the runs,
the latest evaluation scores, and one expandable entry per ingested document showing
exactly what happened to it: input path, sections, every table and what it became,
pages by type, chunks, links by verb and each verification check. No scripts, no
network: the file opens straight from the work volume.
"""

from __future__ import annotations

import html
import time
from collections import Counter, defaultdict
from pathlib import Path

from docingest.reporting import run_history

# How many document entries the page shows in full. Older entries are left out to keep the file
# small.
MAX_DOCUMENTS = 500  # newest document entries shown in full

# Styles for the page. Colours are CSS variables, with a separate set for dark mode.
CSS = """
:root{--bg:#fbfaf8;--panel:#ffffff;--ink:#1f1e1c;--quiet:#6b6863;--rule:#e4e1db;
--accent:#2f6fde;--good:#2e8540;--warn:#b7791f;--bad:#c0392b;--bar:#c9d8f5}
@media (prefers-color-scheme: dark){:root{--bg:#1b1a19;--panel:#242321;--ink:#ecebe8;
--quiet:#a3a09a;--rule:#3a3835;--accent:#7aa5f0;--good:#6cc17c;--warn:#e0b05a;
--bad:#ef7a6d;--bar:#34507f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
h3{font-size:14px;margin:16px 0 6px}
.sub{color:var(--quiet);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:12px}
.card b{display:block;font-size:22px}.card span{color:var(--quiet);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px}
.panel{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:12px}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--rule);vertical-align:top}
th{color:var(--quiet);font-weight:600;font-size:12px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.bar{background:var(--bar);height:10px;border-radius:3px;min-width:2px}
.st{font-weight:600;font-size:12px}.pass{color:var(--good)}.warn{color:var(--warn)}
.fail,.failed{color:var(--bad)}.info{color:var(--quiet)}
details{background:var(--panel);border:1px solid var(--rule);border-radius:8px;margin:8px 0}
summary{cursor:pointer;padding:10px 12px;display:flex;gap:12px;flex-wrap:wrap}
summary .f{font-weight:600}summary .m{color:var(--quiet)}
details>div{padding:0 12px 12px}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
.days{display:flex;align-items:flex-end;gap:3px;height:90px}
.days div{flex:1;max-width:28px;background:var(--accent);border-radius:2px 2px 0 0;min-height:1px}
.muted{color:var(--quiet)}
"""


# Escape a value for HTML, so it shows as text and cannot inject markup. None becomes empty.
def _e(value) -> str:
    return html.escape("" if value is None else str(value))


# A coloured status label (PASS, WARN, FAIL and so on) for a check or run.
def _status(s) -> str:
    s = (s or "").lower()
    return f'<span class="st {_e(s.split()[0] if s else "")}">{_e(s.upper() or "-")}</span>'


# A small table of counts, sorted largest first, with a bar scaled to the biggest count.
def _count_table(title: str, counts: Counter, label: str) -> str:
    top = counts.most_common()
    peak = max((n for _, n in top), default=1)
    rows = "".join(
        f'<tr><td>{_e(k)}</td><td class="n">{n}</td>'
        f'<td style="width:45%"><div class="bar" style="width:{100 * n / peak:.0f}%"></div></td></tr>'
        for k, n in top
    )
    return (
        f'<div class="panel"><h3>{_e(title)}</h3><table><tr><th>{_e(label)}</th>'
        f'<th class="n">Documents</th><th></th></tr>{rows or "<tr><td>-</td></tr>"}</table></div>'
    )


# Keep only the newest record of each document, so totals count each file once.
def _latest_per_document(docs: list[dict]) -> list[dict]:
    """The newest record of each document (by project and source path)."""
    latest: dict[tuple, dict] = {}
    for d in docs:
        latest[(d.get("project"), d.get("source_path"))] = d
    return list(latest.values())


# A bar chart of documents processed per day, for the last 30 active days.
def _activity(docs: list[dict]) -> str:
    per_day = Counter((d.get("recorded_at") or "")[:10] for d in docs if d.get("recorded_at"))
    if not per_day:
        return '<p class="muted">No runs yet.</p>'
    days = sorted(per_day)[-30:]
    peak = max(per_day[d] for d in days)
    bars = "".join(
        f'<div title="{_e(d)}: {per_day[d]} document(s)" '
        f'style="height:{100 * per_day[d] / peak:.0f}%"></div>'
        for d in days
    )
    return (
        f'<div class="panel"><div class="days">{bars}</div>'
        f'<p class="muted">Documents processed per day, {_e(days[0])} to {_e(days[-1])} '
        f"(last {len(days)} active days; hover a bar for its count).</p></div>"
    )


# The evaluation table: the newest score for each answer key.
def _evals(evals: list[dict]) -> str:
    if not evals:
        return (
            '<p class="muted">No evaluations yet. Run <code>docingest eval</code> to score the '
            "pipeline against the answer keys in <code>answer_keys/</code>.</p>"
        )
    latest: dict[str, dict] = {}
    for e in evals:
        # Later rows replace earlier ones, so each answer key shows its newest evaluation.
        latest[e["answer_key"]] = e
    head = (
        "<tr><th>Answer key</th><th>Evaluated</th><th class='n'>Overall</th>"
        "<th class='n'>Sections F1</th><th class='n'>Tables</th><th class='n'>Entities F1</th>"
        "<th class='n'>Attributes</th><th class='n'>Links F1</th><th class='n'>Retrieval</th></tr>"
    )

    # Format a 0-to-1 score as a whole percentage, or "-" when there is no score.
    def pct(v):
        return "-" if v is None else f"{100 * v:.0f}%"

    rows = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(e.get('evaluated_at'))}</td>"
        f"<td class='n'><b>{pct(e.get('overall'))}</b></td>"
        f"<td class='n'>{pct(e.get('sections_f1'))}</td><td class='n'>{pct(e.get('tables_accuracy'))}</td>"
        f"<td class='n'>{pct(e.get('entities_f1'))}</td><td class='n'>{pct(e.get('attributes_accuracy'))}</td>"
        f"<td class='n'>{pct(e.get('links_f1'))}</td><td class='n'>{pct(e.get('retrieval_hit_rate'))}</td></tr>"
        for k, e in sorted(latest.items())
    )
    return f'<div class="panel scroll"><table>{head}{rows}</table></div>'


# The runs table: one row per run, with its project, team, counts and verification status.
def _runs(runs: list[dict]) -> str:
    head = (
        "<tr><th>Run</th><th>Started</th><th>Project</th><th>Team</th><th>By</th>"
        "<th>Input path</th><th class='n'>Docs</th><th class='n'>Failed</th>"
        "<th class='n'>Pages</th><th class='n'>Chunks</th><th class='n'>Links</th>"
        "<th>Checks</th><th class='n'>Seconds</th></tr>"
    )
    rows = "".join(
        f"<tr><td><code>{_e(r.get('run_id'))}</code></td><td>{_e(r.get('started_at'))}</td>"
        f"<td>{_e(r.get('project'))}</td><td>{_e(r.get('team'))}</td><td>{_e(r.get('submitted_by'))}</td>"
        f"<td><code>{_e(r.get('input_path'))}</code></td><td class='n'>{_e(r.get('documents'))}</td>"
        f"<td class='n'>{_e(r.get('failed'))}</td><td class='n'>{_e(r.get('pages'))}</td>"
        f"<td class='n'>{_e(r.get('chunks'))}</td><td class='n'>{_e(r.get('links'))}</td>"
        f"<td>{_status(r.get('verification'))}</td><td class='n'>{_e(r.get('seconds'))}</td></tr>"
        # The newest 200 runs, most recent first.
        for r in reversed(runs[-200:])
    )
    return f'<div class="panel scroll"><table>{head}{rows}</table></div>'


# A two-column table of label and value, used for the details of one document.
def _kv(pairs: list[tuple[str, object]]) -> str:
    return (
        "<table>"
        + "".join(f"<tr><th style='width:160px'>{_e(k)}</th><td>{v}</td></tr>" for k, v in pairs)
        + "</table>"
    )


# One expandable entry for a document: a summary line, then its input, tables, pages, links,
# checks, sections and warnings.
def _document(d: dict) -> str:
    """One expandable entry: everything recorded about one document in one run."""
    # A failed document has no content or checks, so its entry shows only its identity and the
    # error.
    failed = d.get("status") == "failed"
    summary = (
        f"<summary>{_status(d.get('status'))}<span class='f'>{_e(d.get('file_name'))}</span>"
        f"<span class='m'>{_e(d.get('project'))} · {_e(d.get('kind') or '-')} · "
        f".{_e(d.get('extension'))} · {_e(d.get('recorded_at'))}</span>"
        + (
            ""
            if failed
            else f"<span class='m'>{_e(d.get('chunks'))} chunks · {_e(d.get('entities'))} "
            f"entities · {_e(d.get('links'))} links</span>"
        )
        + "</summary>"
    )
    ident = _kv(
        [
            ("Run", f"<code>{_e(d.get('run_id'))}</code>"),
            ("Project / team", f"{_e(d.get('project'))} / {_e(d.get('team') or '-')}"),
            ("Submitted by", _e(d.get("submitted_by"))),
            ("Input file", f"<code>{_e(d.get('source_path'))}</code>"),
            ("Size", f"{_e(d.get('size_bytes'))} bytes"),
            ("SHA-256", f"<code>{_e(d.get('sha256'))}</code>"),
            ("Attachment of", _e(d.get("attachment_of") or "-")),
            ("Document key", f"<code>{_e(d.get('key'))}</code>"),
            ("Title", _e(d.get("title"))),
            ("Converter", _e(d.get("converter"))),
            ("Seconds", _e(d.get("seconds"))),
        ]
    )
    if failed:
        return f"<details>{summary}<div>{ident}<h3>Error</h3><p class='fail'>{_e(d.get('error'))}</p></div></details>"
    tables = "".join(
        f"<tr><td class='n'>{_e(t.get('section'))}</td><td>{_e(', '.join(t.get('columns', [])))}</td>"
        f"<td class='n'>{_e(t.get('rows'))}</td><td>{_e(t.get('became'))}</td></tr>"
        for t in d.get("tables", [])
    )
    pages = "".join(
        f"<tr><td>{_e(k)}</td><td class='n'>{n}</td></tr>"
        for k, n in sorted(d.get("pages_by_type", {}).items(), key=lambda x: -x[1])
    )
    links = "".join(
        f"<tr><td>{_e(k)}</td><td class='n'>{n}</td></tr>"
        for k, n in sorted(d.get("links_by_verb", {}).items(), key=lambda x: -x[1])
    )
    checks = "".join(
        f"<tr><td>{_e(c['name'])}</td><td>{_status(c['status'])}</td><td>{_e(c['detail'])}</td></tr>"
        for c in d.get("checks", [])
    )
    sections = "".join(f"<li>{_e(s)}</li>" for s in d.get("sections", []))
    warnings = "".join(f"<li>{_e(w)}</li>" for w in d.get("warnings", []))
    return (
        f"<details>{summary}<div>{ident}"
        f"<h3>Tables ({len(d.get('tables', []))})</h3><div class='scroll'><table><tr><th class='n'>Section</th>"
        f"<th>Columns</th><th class='n'>Rows</th><th>Became</th></tr>{tables}</table></div>"
        f"<div class='grid'><div><h3>Pages by type ({_e(d.get('pages'))})</h3><table>{pages}</table></div>"
        f"<div><h3>Links by verb ({_e(d.get('links'))})</h3><table>{links}</table></div></div>"
        f"<h3>Verification checks</h3><div class='scroll'><table>{checks}</table></div>"
        f"<h3>Sections ({len(d.get('sections', []))})</h3><ol>{sections}</ol>"
        + (f"<h3>Warnings</h3><ul>{warnings}</ul>" if warnings else "")
        + "</div></details>"
    )


# Build the whole page from the run history in the work folder. Returns the HTML text.
def render(work: Path) -> str:
    """The whole page as HTML."""
    runs = run_history.read(work, "runs")
    docs = run_history.read(work, "documents")
    evals = run_history.read(work, "evals")
    latest = _latest_per_document(docs)
    # Failed documents do not count towards the content totals.
    ok = [d for d in latest if d.get("status") != "failed"]
    total = Counter()
    for d in ok:
        total.update(
            pages=d.get("pages") or 0,
            chunks=d.get("chunks") or 0,
            links=d.get("links") or 0,
            entities=d.get("entities") or 0,
            tables=len(d.get("tables", [])),
        )
    # The headline numbers shown as cards at the top of the page.
    cards = [
        ("Documents", len(latest), "distinct input files, latest state"),
        ("Runs", len(runs), "ingestion runs recorded"),
        ("Projects", len({d.get("project") for d in latest}), "with at least one document"),
        ("Failed now", sum(d.get("status") == "failed" for d in latest), "latest attempt failed"),
        ("Tables", total["tables"], "found in documents"),
        ("Entities", total["entities"], "typed entity pages"),
        ("Chunks", total["chunks"], "stored in the brain"),
        ("Links", total["links"], "typed links written"),
    ]
    card_html = "".join(
        f'<div class="card"><b>{_e(v)}</b>{_e(k)}<br><span>{_e(note)}</span></div>'
        for k, v, note in cards
    )
    # Group the latest record of each document under its project.
    by_project: dict[str, list[dict]] = defaultdict(list)
    for d in latest:
        by_project[d.get("project") or "unassigned"].append(d)
    project_rows = "".join(
        f"<tr><td>{_e(p)}</td><td class='n'>{len(ds)}</td>"
        f"<td class='n'>{sum((d.get('chunks') or 0) for d in ds)}</td>"
        f"<td class='n'>{sum((d.get('links') or 0) for d in ds)}</td>"
        f"<td class='n'>{sum(d.get('status') == 'failed' for d in ds)}</td>"
        f"<td>{_e(max((d.get('recorded_at') or '') for d in ds))}</td></tr>"
        for p, ds in sorted(by_project.items())
    )
    # Four breakdown panels: by document kind, file type, result and team.
    breakdowns = "".join(
        [
            _count_table(
                "By document kind", Counter(d.get("kind") or "failed" for d in latest), "Kind"
            ),
            _count_table("By file type", Counter(d.get("extension") for d in latest), "Extension"),
            _count_table("By result", Counter(d.get("status") for d in latest), "Status"),
            _count_table("By team", Counter(d.get("team") or "-" for d in latest), "Team"),
        ]
    )
    # The newest documents, in full, most recent first.
    doc_entries = "".join(_document(d) for d in reversed(docs[-MAX_DOCUMENTS:]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>docingest run history</title><style>{CSS}</style></head>
<body><main>
<h1>docingest run history</h1>
<p class="sub">Generated {_e(time.strftime("%Y-%m-%d %H:%M:%S %z"))} from
<code>{_e(run_history.history_dir(work))}</code>. Totals use each document's latest run.</p>
<div class="cards">{card_html}</div>
<h2>Projects</h2>
<div class="panel scroll"><table><tr><th>Project</th><th class="n">Documents</th>
<th class="n">Chunks</th><th class="n">Links</th><th class="n">Failed</th><th>Last activity</th></tr>
{project_rows or '<tr><td class="muted">No documents yet.</td></tr>'}</table></div>
<h2>Breakdown</h2><div class="grid">{breakdowns}</div>
<h2>Activity</h2>{_activity(docs)}
<h2>Evaluation</h2>{_evals(evals)}
<h2>Runs</h2>{_runs(runs)}
<h2>Documents</h2>
<p class="muted">Newest first, one entry per document per run (up to {MAX_DOCUMENTS}).
Open an entry to see its input, tables, pages, links and checks.</p>
{doc_entries or '<p class="muted">No documents yet.</p>'}
</main></body></html>
"""
