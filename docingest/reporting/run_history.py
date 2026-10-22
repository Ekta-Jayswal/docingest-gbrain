# What this file does: keeps the append-only history of every ingest run, document and
# evaluation, as JSON-lines files. The dashboard and the recap are both built from it.

"""Run history: an append-only record of every ingestion run, document and evaluation.

Three JSON-lines files under ``<work>/run_history/``, one JSON object per line:

- ``runs.jsonl``       one line per run: who, which project, input path, totals, status
- ``documents.jsonl``  one line per document per run: input file, kind, sections, tables
                       and what each became, pages by type, chunks, links by verb, checks
- ``evals.jsonl``      one line per evaluated document (``docingest eval``)

Lines are only ever appended, so the history survives re-runs and several teams can
share one work volume. The dashboard (``dashboard_page.py``) is built from these files.
"""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import socket
import time
import uuid
from collections import Counter
from pathlib import Path

from docingest.utils import clock

# Version of the run-history row format. Change it if the saved fields change.
HISTORY_VERSION = 1


# The folder that holds the three history files, inside the work folder.
def history_dir(work: Path) -> Path:
    """The run-history folder for a work directory."""
    return work / "run_history"


# Create a run ID that sorts by time and is unlikely to repeat.
def new_run_id() -> str:
    """A sortable, unique run ID: UTC time plus a short random suffix."""
    return clock.compact() + "-" + uuid.uuid4().hex[:6]


# Name the person who ran the command, for the history.
def submitter(explicit: str | None) -> str:
    """Who started the run: the flag, then DOCINGEST_USER, then the OS user."""
    if explicit:
        return explicit
    if os.environ.get("DOCINGEST_USER"):
        return os.environ["DOCINGEST_USER"]
    try:
        return getpass.getuser()
    except (KeyError, OSError):  # no passwd entry, as in some containers
        return "unknown"


# Add rows to the end of a history file. Existing lines are never changed.
def append(work: Path, name: str, rows: list[dict]) -> None:
    """Append rows to ``<work>/run_history/<name>.jsonl`` under an exclusive file lock."""
    if not rows:
        return
    folder = history_dir(work)
    folder.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    with open(folder / f"{name}.jsonl", "a", encoding="utf-8") as f:
        # Lock the file while writing, so two runs writing at the same time do not mix their lines.
        # The lock is not available on Windows, where the single write below is used alone.
        with contextlib.suppress(ImportError, OSError):
            import fcntl  # not on Windows; there the single write below is still one call

            fcntl.flock(f, fcntl.LOCK_EX)
        f.write(data)


# Read every row of a history file. A line that is not valid JSON is skipped, not fatal.
def read(work: Path, name: str) -> list[dict]:
    """All rows of a run-history file; unreadable lines are skipped."""
    path = history_dir(work) / f"{name}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            rows.append(json.loads(line))
    return rows


# Summarise one table for the history: its section, columns, row count and what it became.
def _table_row(t: dict) -> dict:
    return {
        "section": t["section"] + 1 if isinstance(t.get("section"), int) else None,  # 1-based
        "columns": [c.strip("* ") for c in t.get("header", [])],
        "rows": len(t.get("rows", [])),
        "became": t.get("mapped_to"),
    }


# Build the history record for one document, from its manifest and its verification result.
def document_record(
    work: Path, key: str, verified: dict | None, seconds: float | None, parent: str | None
) -> dict:
    """What happened to one document, from its manifest and its verification result."""
    m = json.loads((work / "manifests" / f"{key}.json").read_text())
    src = Path(m["source_path"])
    # Count pages per type, and links per verb, for the dashboard.
    pages_by_type = Counter(p["type"] for p in m["pages"])
    links_by_verb = Counter(v for p in m["pages"] for v, ts in p["links"].items() for _ in ts)
    checks = (verified or {}).get("checks", [])
    return {
        "key": key,
        "title": m["title"],
        "kind": m["kind"],
        "source_path": str(src),
        "file_name": src.name,
        "extension": src.suffix.lower().lstrip(".") or "none",
        "size_bytes": src.stat().st_size if src.exists() else None,
        "sha256": m["sha256"],
        "attachment_of": parent,
        "converter": m["converter"],
        "warnings": m["warnings"],
        "sections": [s.get("heading") for s in m["sections"]],
        "tables": [_table_row(t) for t in m["tables"]],
        "pages": len(m["pages"]),
        "pages_by_type": dict(pages_by_type),
        "entities": sum(
            n for t, n in pages_by_type.items() if t not in ("source_document", "document_section")
        ),
        "links": sum(links_by_verb.values()),
        "links_by_verb": dict(links_by_verb),
        # Every distinct link as "source page|verb|target page", so the dashboard can count
        # unique links across documents (shared entities are linked from more than one).
        "link_keys": sorted(
            {f"{p['slug']}|{v}|{t}" for p in m["pages"] for v, ts in p["links"].items() for t in ts}
        ),
        "chunks": (verified or {}).get("chunks"),
        "status": (verified or {}).get("status", "not verified"),
        "checks": [
            {"name": c["name"], "status": c["status"], "detail": c["detail"]} for c in checks
        ],
        "seconds": seconds,
        "llm": _llm_row(m.get("llm_report")),
    }


# What the --llm option did for one document: whether it ran, sections sent, items resolved.
def _llm_row(rep: dict | None) -> dict:
    if not rep:
        return {"used": False}
    return {
        "used": True,
        "mode": rep.get("mode"),
        "model": rep.get("model"),
        "sections_sent": rep.get("sections", 0),
        "accepted": rep.get("accepted", 0),
        "rejected": rep.get("rejected", 0),
        "errors": len(rep.get("errors", [])),
    }


# Save one run to the history: a row per document (including failed files), then the run row.
def record_ingest(
    work: Path,
    brain: Path,
    run: dict,
    result: dict,
    report: dict | None,
    embed_report: dict | None = None,
) -> dict:
    """Append one run and its documents to the run history. Returns the run row.

    ``run`` holds the run's identity: run_id, started_at, project, team, submitted_by,
    input_path, command.
    """
    verified = {d["key"]: d for d in (report or {}).get("documents", [])}
    base = {k: run.get(k) for k in ("run_id", "project", "team", "submitted_by")}
    # The log file of this run, relative to the work folder. The dashboard links to it.
    base["log_file"] = f"logs/runs/{run.get('run_id')}.log"
    docs = [
        {
            **base,
            "recorded_at": clock.iso(),
            **document_record(
                work,
                key,
                verified.get(key),
                result["timings"].get(key),
                result["parents"].get(key),
            ),
        }
        for key in result["processed"]
    ]
    for f in result["failed"]:
        src = Path(f["file"])
        docs.append(
            {
                **base,
                "recorded_at": clock.iso(),
                "key": None,
                "title": src.name,
                "kind": None,
                "source_path": str(src),
                "file_name": src.name,
                "extension": src.suffix.lower().lstrip(".") or "none",
                "size_bytes": src.stat().st_size if src.exists() else None,
                "attachment_of": f.get("parent"),
                "status": "failed",
                "error": f["error"],
            }
        )
    summary = (report or {}).get("summary", {})
    row = {
        **run,
        "log_file": base["log_file"],
        "history_version": HISTORY_VERSION,
        "finished_at": clock.iso(),
        "host": socket.gethostname(),
        "brain": str(brain),
        "documents": len(result["processed"]),
        "failed": len(result["failed"]),
        "deleted_pages": len(result["deleted"]),
        "pages": summary.get("pages"),
        "chunks": summary.get("chunks"),
        "links": summary.get("links"),
        "verification": summary.get("status", "not verified"),
        "embeddings": (embed_report or {}).get("status"),
        # Seconds the run took, from the start time the caller stored in the run dict.
        "seconds": round(time.time() - run["_t0"], 1) if "_t0" in run else None,
    }
    row.pop("_t0", None)
    append(work, "documents", docs)
    append(work, "runs", [row])
    return row
