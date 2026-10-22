# What this file does: the command line. `python -m docingest <command>` lands here. Each cmd_*
# function runs one command (ingest, review, embed, verify, eval, and so on), and main() defines
# every command and its options.

"""Command line: python -m docingest <command> ..."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from docingest.reporting import dashboard_page
from docingest.utils import clock
from docingest.utils import logger as logmod

from docingest.reporting import recap
from docingest.reporting import run_history

from docingest.ingestion import extract_with_ai

from docingest.ingestion import litellm_proxy
from docingest.ingestion.detect_document_kind import classify
from docingest.ingestion.rule_confidence import estimate, route_sections
from docingest.ingestion.convert_to_markdown import ConversionError, convert
from docingest.ingestion.extract_with_rules import Extractor, document_id
from docingest.utils.gbrain_client import GBrain, GBrainError
from docingest.utils.markdown_parsing import slugify
from docingest.ingestion.pipeline import Pipeline, collect_inputs, log
from docingest.ingestion.review_proposals import ReviewBoard, ReviewError, reviewer_name
from docingest.vocabulary.vocabulary_loader import Vocabulary


# Work out the brain folder and the work folder. The brain comes from --brain or GBRAIN_HOME.
# The work folder defaults to a folder named work inside the brain folder.
def _paths(args) -> tuple[Path, Path]:
    home = args.brain or os.environ.get("GBRAIN_HOME")
    if not home:
        sys.exit("Set GBRAIN_HOME or pass --brain <dir> (the brain lives in <dir>/.gbrain)")
    home = Path(home).resolve()
    # The work files (state, manifests, reports) belong to one brain, so they live beside it.
    work = Path(args.work or os.environ.get("DOCINGEST_WORK") or home / "work").resolve()
    logmod.add_file(work)  # every command writes its own log file under work/logs/
    return home, work


# Default embedding model, called through the LiteLLM proxy. EMBEDDING_MODEL in .env.local
# overrides it (a bare proxy model name gets the litellm: prefix), so a proxy that names the
# model differently needs no flag on every command.
DEFAULT_MODEL = os.environ.get("EMBEDDING_MODEL") or "text-embedding-3-small"
if not DEFAULT_MODEL.startswith("litellm:"):
    DEFAULT_MODEL = "litellm:" + DEFAULT_MODEL
# Width of the default model's vectors. The brain stores vectors at this width, so it must match
# the model.
DEFAULT_DIMS = int(os.environ.get("EMBEDDING_DIMENSIONS") or 1536)


# The embedding model and width from the command line. Used by every command that embeds.
def _embedding(args) -> tuple[str, int]:
    """The embedding model and width chosen on the command line (or the defaults)."""
    return args.embedding_model, args.embedding_dimensions


# Print the embedding result: the overall status, then one line per check.
def _print_embed_report(report: dict, work: Path) -> None:
    """Log the embedding report."""
    log(f"Embedding verification: {report['status'].upper()} ({report['seconds']} s)")
    for c in report["checks"]:
        log(f"  {c['status'].upper():4} {c['name']}: {c['detail']}")
    log(f"Report: {work / 'reports' / 'embeddings-latest.md'}")


# The `ingest` command. It checks the inputs and options, runs the pipeline, verifies the result,
# optionally embeds, then saves the run history, rebuilds the dashboard and prints the recap.
def cmd_ingest(args) -> int:
    """Ingest a file or folder, then verify it. Returns the process exit code."""
    home, work = _paths(args)
    if args.llm_all:
        args.llm = True
    # The AI step needs an API key. Stop here, before any work starts, rather than failing partway
    # through.
    if args.llm and not extract_with_ai.credentials_available():
        sys.exit("--llm needs LITELLM_BASE_URL and LITELLM_API_KEY in .env.local")
    source = Path(args.path).resolve()
    if not source.exists():
        sys.exit(f"No such file or directory: {source}")
    # The work and brain folders are excluded, so the tool never ingests its own output.
    inputs, skipped = collect_inputs(source, exclude=[work, home])
    for s in skipped:
        log(f"Skipping unsupported file: {s}")
    if not inputs:
        sys.exit(f"No supported documents found under {source}")
    log(f"{len(inputs)} document(s) to ingest into {home}")
    # The identity of this run, saved with the history. _t0 is the start time, used for the
    # duration and then removed.
    run = {
        "run_id": logmod.current_run() or run_history.new_run_id(),
        "started_at": clock.iso(),
        "project": args.project or os.environ.get("DOCINGEST_PROJECT") or "unassigned",
        "team": args.team or os.environ.get("DOCINGEST_TEAM"),
        "submitted_by": run_history.submitter(args.submitted_by),
        "input_path": str(source),
        "command": "docingest " + " ".join(args.argv),
        "_t0": time.time(),
    }
    if run["project"] == "unassigned":
        log("No project given (--project or DOCINGEST_PROJECT); recording it as 'unassigned'")
    log(f"Run {run['run_id']}: project {run['project']}, submitted by {run['submitted_by']}")

    embedding = _embedding(args) if args.embed else None
    # Check the embedding setup before the run starts, so a bad proxy or brain fails early.
    if embedding:
        gb = GBrain(home)
        # A brain created without embeddings cannot take them later, so refuse and ask for a new
        # folder.
        if gb.exists() and not gb.embeddings_enabled():
            sys.exit(
                "--embed: this brain was created with embeddings off. "
                'Use a NEW GBRAIN_HOME folder (see COMMANDS.md, "Remove or start over").'
            )
        # Check that the proxy is reachable and offers the model.
        ok, msg = litellm_proxy.check(*embedding)
        if not ok:
            sys.exit(f"--embed: {msg}")
    # The pipeline does the work. With --llm-all every section goes to the AI; with --llm only the
    # low-confidence ones do.
    pipe = Pipeline(
        work,
        home,
        use_llm=args.llm,
        llm_effort=args.llm_effort,
        kind=args.kind,
        llm_mode="all" if args.llm_all else "auto",
        embedding=embedding,
        schema_mode=args.schema_mode,
    )
    try:
        result = pipe.ingest(inputs)
    except ReviewError as e:
        sys.exit(str(e))
    # Any file that failed makes the exit code 1.
    exit_code = 1 if result["failed"] else 0
    verify_failed = False
    for f in result["failed"]:
        log(f"Failed: {f['file']}: {f['error']}")

    report = embed_report = None
    # Verify the documents that were processed. --no-verify skips this step.
    if not args.no_verify and result["processed"]:
        log("Verifying ingestion ...")
        report = pipe.verify(result["processed"], args.search_checks)
        print_summary(report, work)
        # A failed verification fails the run, and blocks the embeddings step.
        if report["summary"]["status"] == "fail":
            exit_code = 1
            verify_failed = True
    # Embeddings are made only after verification passes, so no vectors are made for broken pages.
    if embedding:
        if not result["processed"] or verify_failed:
            log("Skipping embeddings: nothing was ingested, or verification failed")
        else:
            code, embed_report = _embed(pipe, embedding, args.max_chunks, args.queries, work)
            exit_code = code or exit_code
    # Score the ingested documents against their answer keys, unless --no-eval.
    evals = None if args.no_eval else _evaluate(work, result["processed"])
    # Save the run to the history, then rebuild the dashboard, then print the recap.
    run_history.record_ingest(work, home, run, result, report, embed_report)
    log(f"Run recorded in {run_history.history_dir(work)}")
    _write_dashboard(work)
    for line in recap.lines(work, result, report, embed_report, run["run_id"], evals):
        log(line)
    # A successful run that left proposals for a reviewer uses a special exit code, so scripts can
    # notice.
    if result["proposals_pending"] and not exit_code and args.pending_exit_code:
        exit_code = args.pending_exit_code
    return exit_code


# ---- review ----------------------------------------------------------------------------


# Ask a yes or no question. Without a terminal it does not guess; --yes is required instead.
def _confirm(args, question: str) -> bool:
    """True after the reviewer says yes (or --yes); without a terminal, --yes is required."""
    if args.yes:
        return True
    if not sys.stdin.isatty():
        sys.exit("Not a terminal: add --yes to confirm")
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# Record a reviewer's decision. It checks the decision, shows what it would change, asks for
# confirmation, then records it. The decision is applied later, not now.
def _decide(board: ReviewBoard, args, text: str) -> int:
    """Show the preview of a decision, ask, and record it."""
    pid = args.id
    p = board.pending().get(pid)
    if p is None:
        sys.exit(f"{pid} is not open. List the open ones with: review list")
    by = reviewer_name(args.by)
    try:
        action = board.parse(p, text)
        board.build(p, action, board.vocab(), board.learned())
        impact = board.preview(p, action)
    except ReviewError as e:
        sys.exit(f"Refused: {e}")
    need, note_for = board.required(p, action, impact)
    print(board.render(p))
    print(f"Your decision: {text}  ->  {board._describe(action) if action['action'] != 'reject' else 'reject (stays generic)'}")
    print(f"Changes: {impact['pages']} page(s) in {impact['documents']} document(s)")
    for x in impact["examples"]:
        print(f"  e.g. {x}")
    # Some decisions (a new type, a rejection, or going against the suggestion) need a written
    # reason.
    if note_for and not args.note:
        sys.exit(f"Refused: {note_for} needs a reason. Add --note \"...\"")
    # Some decisions need more than one reviewer to approve.
    if need > 1:
        print(f"This needs {need} different reviewers.")
    if not _confirm(args, "Record this decision?"):
        print("Nothing recorded.")
        return 1
    # Save the decision. It is not applied until the next ingest or `review apply`.
    p = board.record(pid, text, by, args.note)
    board.write_pending_md()
    action, why = board.ready(p, board.vocab())
    print(f"Recorded for {by}." + ("" if action else f" Not applied yet: {why}."))
    print("It is applied by the next `ingest` or by: ./ingest.sh review apply")
    _write_dashboard(board.work)
    return 0


# The `review` command: list, show, approve, map, reject, apply, revert or log the schema
# proposals.
def cmd_review(args) -> int:
    """Review commands: list, show, approve, map, reject, apply, revert, log."""
    home, work = _paths(args)
    board = ReviewBoard(work)
    what = args.review_command
    # List the open proposals, and say which page to read them on.
    if what == "list":
        board.sync_files()
        rows = board.pending()
        if not rows:
            print("Nothing to review.")
        for pid, p in rows.items():
            print(f"{pid}  {p['kind'].replace('_', ' '):11} {p['found']}  -> {board._describe(p['suggestion'])} "
                  f"[{p['suggestion']['by']}; {len(p['approvals'])} approval(s)]")  # fmt: skip
        print(f"\nPage to read: {board.write_pending_md()}")
        return 0
    if what == "show":
        try:
            p = board.get(args.id)
            if p.get("status") is None and p["decision"] == "pending":
                board.preview(p, board.parse(p, "approve")) if p["suggestion"]["action"] != "none" else None
        except ReviewError as e:
            sys.exit(str(e))
        print(board.render(p))
        return 0
    if what == "log":
        for e in board.history()[-args.limit :]:
            print(json.dumps(e, ensure_ascii=False))
        return 0
    # A decision. Approve, map and reject all go through _decide, except --all-suggested, which
    # accepts every suggestion that maps to an existing type or verb.
    if what in ("approve", "map", "reject"):
        if args.all_suggested:
            ids = [
                pid for pid, p in board.pending().items()
                if p["suggestion"]["action"] in ("alias", "map_type")
            ]  # fmt: skip
            if not ids:
                print("No pending suggestion maps to an existing type or verb.")
                return 0
            print(f"Accepting the suggestion for: {', '.join(ids)}")
            if not _confirm(args, "Record all of them?"):
                return 1
            for pid in ids:
                p = board.pending()[pid]
                try:
                    board.record(pid, "approve", reviewer_name(args.by), args.note)
                except ReviewError as e:
                    print(f"  {pid}: refused: {e}")
            board.write_pending_md()
            _write_dashboard(board.work)
            return 0
        if not args.id:
            sys.exit("Give a proposal ID, or --all-suggested")
        text = (
            "reject" if what == "reject"
            else f"map:{args.target}" if what == "map"
            else f"rename:{args.name}" if args.name else "approve"
        )  # fmt: skip
        return _decide(board, args, text)
    # Apply the recorded decisions now. This is the same step an ingest runs at its start.
    if what == "apply":
        from docingest.ingestion.review_proposals import now

        pipe = Pipeline(work, home, schema_mode=args.schema_mode)
        try:
            result = pipe.ingest([])
        except ReviewError as e:
            sys.exit(str(e))
        print(f"Applied: {', '.join(result['proposals_applied']) or 'nothing'}")
        if result["proposals_rolled_back"]:
            print(f"Rolled back (verification failed): {', '.join(result['proposals_rolled_back'])}")
        print(f"Still open: {', '.join(result['proposals_pending']) or 'none'}  ({now()})")
        _write_dashboard(work)
        return 1 if result["proposals_rolled_back"] else 0
    # Undo one applied decision: put the earlier vocabulary back, read the affected documents
    # again, and rebuild the shared pages.
    if what == "revert":
        pipe = Pipeline(work, home, schema_mode=args.schema_mode)
        try:
            with board.lock():
                p = board.revert(args.id, reviewer_name(args.by))
                pipe._reload_vocabulary()
                state = {"processed": [], "failed": [], "stale": [], "timings": {}, "parents": {}}
                pipe._reingest(p["documents"], state)
                pipe._finish(state)
        except ReviewError as e:
            sys.exit(str(e))
        print(f"{args.id} reverted; {len(p['documents'])} document(s) read again with the earlier vocabulary.")
        _write_dashboard(work)
        return 0
    return 1


# Rebuild the dashboard page. It is a convenience, so a failure is logged and never fails the run.
def _write_dashboard(work: Path) -> Path:
    """Regenerate the run-history page; a failure here never fails the run."""
    out = work / "reports" / "dashboard.html"
    try:
        dashboard_page.write(work, out)
        log(f"Dashboard: {out}")
    except Exception as e:  # the page is a convenience; the run history is the record
        log(f"Dashboard not written: {e}")
    return out


# Score every answer key whose document this run ingested, with the same scoring as `eval`.
# Returns the evaluations (an empty list when no answer key matches).
def _evaluate(work: Path, processed: list[str]) -> list[dict]:
    """Run the answer keys for the ingested documents, record them in the history, return them."""
    import yaml

    from docingest.evaluation import answer_key_evaluation

    names = {
        Path(json.loads((work / "manifests" / f"{k}.json").read_text())["source_path"]).name
        for k in processed
    }
    root = answer_key_evaluation.DEFAULT_ANSWERS
    keys = sorted(root.glob("*.yaml")) + sorted((root / "holdout").glob("*.yaml"))
    matched = [k for k in keys if Path(yaml.safe_load(k.read_text())["document"]).name in names]
    if not matched:
        return []
    log(f"Evaluating {len(matched)} answer key(s) ...")
    reports = [answer_key_evaluation.run(k, work) for k in matched]
    run_history.append(work, "evals", [answer_key_evaluation.history_row(r) for r in reports])
    md = answer_key_evaluation.write_reports(reports, work)
    log(f"Evaluation report: {md}")
    return reports


# Run the embedding step through the pipeline. Returns (exit code, report). A GBrainError is
# reported and gives exit code 1.
def _embed(
    pipe: Pipeline, embedding: tuple[str, int], max_chunks: int, queries, work: Path
) -> tuple[int, dict | None]:
    """Embed pending chunks and check them. Returns an exit code and the report."""
    try:
        report = pipe.embed(
            *embedding, max_chunks=max_chunks, queries_file=Path(queries) if queries else None
        )
    except GBrainError as e:
        log(f"Embedding failed: {e}")
        return 1, None
    _print_embed_report(report, work)
    return (1 if report["status"] == "fail" else 0), report


# The `embed` command: embed what is already ingested, then check the embeddings.
def cmd_embed(args) -> int:
    """Embed pending chunks of everything ingested so far, then run the embedding checks."""
    home, work = _paths(args)
    pipe = Pipeline(work, home)
    # There must be something to embed. If not, the brain or work folder is probably the wrong one.
    if not pipe.state.documents:
        sys.exit("Nothing to embed: no documents recorded in this brain's work directory")
    ok, msg = litellm_proxy.check(*_embedding(args))
    if not ok:
        sys.exit(msg)
    return _embed(pipe, _embedding(args), args.max_chunks, args.queries, work)[0]


# The `embeddings-setup` command. It creates a brain with embeddings, or confirms that an existing
# brain already uses this setup.
def cmd_embeddings_setup(args) -> int:
    """Check the proxy, then create the brain with embeddings or switch an existing one."""
    home, _ = _paths(args)
    model, dims = _embedding(args)
    ok, msg = litellm_proxy.check(model, dims)
    log(("OK: " if ok else "FAILED: ") + msg)
    if not ok:
        return 1
    gb = GBrain(home)
    cfg = gb.config()
    # No brain yet: create one with this embedding setup.
    if not gb.exists():
        gb.ensure_initialized((model, dims))
        log(f"Created a new brain at {home} with {model} at {dims} dimensions")
    # The brain already uses this exact model and width, so nothing needs to change.
    elif gb.embeddings_enabled() and (
        cfg.get("embedding_model"),
        cfg.get("embedding_dimensions"),
    ) == (model, dims):
        log("This brain already uses this embedding model")
    else:
        # gbrain cannot re-embed onto a model it has no price for (LiteLLM models), and the
        # vector width is fixed when the brain is created, so a new brain is the way.
        log(
            f"The brain at {home} was created with a different embedding setup "
            f"({cfg.get('embedding_model') or 'embeddings off'}) and cannot be switched here. "
            "Point GBRAIN_HOME at a NEW folder and run this again; re-ingesting takes seconds."
        )
        return 1
    return 0


# The `doctor` command: check Python, packages, gbrain, bun, LibreOffice, the brain folder, the
# proxy and the API key. Each line is PASS, WARN, FAIL or INFO. Returns 1 if a required check
# fails.
def cmd_doctor(args) -> int:
    """Check that this machine is ready to run docingest. Returns 1 if a required check fails."""
    import importlib
    import shutil

    rows: list[tuple[str, str, str]] = []

    # Collect one check line for the table printed at the end.
    def add(status: str, name: str, detail: str) -> None:
        rows.append((status, name, detail))

    # The tool needs Python 3.12 or newer.
    v = sys.version_info
    add(
        "PASS" if v >= (3, 12) else "FAIL",
        "python",
        f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 12) else " (need 3.12+)"),
    )
    missing = []
    # The Python packages the tool imports. A missing one means setup.sh has not been run.
    for mod in (
        "bs4",
        "markdownify",
        "mammoth",
        "docx",
        "pdfplumber",
        "openpyxl",
        "pptx",
        "extract_msg",
        "mcp",
        "yaml",
    ):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    add(
        "FAIL" if missing else "PASS",
        "python packages",
        f"missing: {', '.join(missing)}. Run ./setup.sh" if missing else "all importable",
    )
    home = args.brain or os.environ.get("GBRAIN_HOME")
    gb = GBrain(Path(home).resolve() if home else Path.cwd() / "brain")
    gbin = Path(gb.binary)
    # The gbrain program counts as found when it exists as a file or is on PATH.
    if gbin.exists() or shutil.which(gb.binary):
        ver = gb.run("--version", check=False).stdout.strip() or "?"
        add(
            "PASS" if "0.60.281" in ver else "WARN",
            "gbrain",
            f"{ver} at {gb.binary}" + ("" if "0.60.281" in ver else " (tested with 0.60.281.0)"),
        )
    else:
        add("FAIL", "gbrain", "not found. Install gbrain or set GBRAIN_BIN=/path/to/gbrain")
    add(
        "PASS" if shutil.which("bun") or (Path.home() / ".bun/bin/bun").exists() else "WARN",
        "bun",
        "found"
        if shutil.which("bun") or (Path.home() / ".bun/bin/bun").exists()
        else "not on PATH (gbrain may still work)",
    )
    add(
        "PASS" if shutil.which("soffice") else "INFO",
        "libreoffice",
        "found" if shutil.which("soffice") else "not installed; only legacy .doc files need it",
    )
    # The brain checks need a home folder. Without one, say so and suggest ./ingest.sh.
    if home:
        h = Path(home).resolve()
        add(
            "PASS",
            "brain folder",
            f"{h} ({'exists' if gb.exists() else 'will be created on first use'})",
        )
        if gb.exists():
            cfg = gb.config()
            add(
                "INFO",
                "brain embeddings",
                f"{cfg.get('embedding_model')} at {cfg.get('embedding_dimensions')} dims"
                if gb.embeddings_enabled()
                else "off",
            )
    else:
        add("FAIL", "brain folder", "GBRAIN_HOME not set. Use ./ingest.sh, which sets it")
    # The proxy is needed only for embeddings, so when it is not set this is information, not a
    # failure.
    base, key = litellm_proxy.settings()
    if base:
        ok, msg = litellm_proxy.check(args.embedding_model, args.embedding_dimensions)
        add("PASS" if ok else "FAIL", "litellm proxy", msg)
    else:
        add("INFO", "litellm proxy", "LITELLM_BASE_URL not set (only needed for embeddings)")
    add(
        "INFO",
        "llm proxy (--llm)",
        "set (--llm possible)"
        if extract_with_ai.credentials_available()
        else "not set (rule-only ingestion; fine)",
    )
    # Line the check names up in columns.
    width = max(len(r[1]) for r in rows)
    for status, name, detail in rows:
        print(f"{status:5} {name:{width}}  {detail}")
    return 1 if any(r[0] == "FAIL" for r in rows) else 0


# The `verify` command: check documents that are already in the brain. With no keys given, every
# recorded document is checked.
def cmd_verify(args) -> int:
    """Re-verify ingested documents (all, or the given keys)."""
    home, work = _paths(args)
    pipe = Pipeline(work, home)
    keys = args.keys or sorted(pipe.state.documents)
    if not keys:
        sys.exit("Nothing to verify: no documents recorded in the work directory's state.json")
    report = pipe.verify(keys, args.search_checks)
    print_summary(report, work)
    return 1 if report["summary"]["status"] == "fail" else 0


# The `plan` command: show what the rules would cover and what the AI would receive, with cost
# estimates. Files are converted in a temporary folder, and nothing is written to the brain.
def cmd_plan(args) -> int:
    """Show per-section rule confidence and AI cost estimates, without touching the brain."""
    import tempfile

    source = Path(args.path).resolve()
    inputs, _ = collect_inputs(source, exclude=[])
    vocab = Vocabulary()
    # The fixed part sent with every AI call (system prompt and schema), used for the estimate.
    system_chars = len(extract_with_ai._system_prompt(vocab)) + len(
        str(extract_with_ai._schema(vocab))
    )
    totals = {"hybrid": [0, 0, 0.0], "all_sections": [0, 0, 0.0]}
    with tempfile.TemporaryDirectory() as tmp:
        for path in inputs:
            try:
                # Attachments go into the temporary folder, so nothing is saved next to the input
                # files.
                conv, reference = convert(path, Path(tmp))
            except ConversionError as e:
                print(f"\n{path.name}: conversion failed ({e})")
                continue
            kind = classify(conv, vocab, args.kind)
            key = slugify(document_id(conv.markdown) or path.stem, 50)
            x = Extractor(vocab)
            res = x.extract(path, conv, reference, kind, key, "")
            routing = route_sections(res, list(x._globals.values()))
            # The list of known items that is sent with every AI call, used for the estimate.
            known_chars = sum(len(p.title) + 40 for p in res.pages) + sum(
                len(g.name) + 40 for g in x._globals.values()
            )
            est = estimate(res, routing, system_chars, known_chars)
            low = [d for d in routing if d["route"] == "ai"]
            print(
                f"\n{path.name} ({kind}): {len(routing)} sections, {len(low)} low-confidence -> AI"
            )
            for d in routing:
                if d["route"] == "ai" or args.verbose:
                    route = "AI   " if d["route"] == "ai" else "rules"
                    print(
                        f"  {route} {d['section']:>3}. {d['heading'][:50]:50} "
                        f"rules cover {d['rule_coverage']:.0%}; {'; '.join(d['reasons'])}"
                    )
            for mode, total in totals.items():
                e = est[mode]
                total[0] += e["sections"]
                total[1] += e["input_tokens"] + e["output_tokens"]
                total[2] += e["cost_usd"]
            h, a = est["hybrid"], est["all_sections"]
            print(
                f"  estimate: --llm ~{h['input_tokens'] + h['output_tokens']:,} tokens"
                f" ~${h['cost_usd']:.2f} | --llm-all ~{a['input_tokens'] + a['output_tokens']:,}"
                f" tokens ~${a['cost_usd']:.2f}"
            )
    h, a = totals["hybrid"], totals["all_sections"]
    print(
        f"\nTotal: --llm sends {h[0]} sections (~{h[1]:,} tokens, ~${h[2]:.2f}); "
        f"--llm-all sends {a[0]} (~{a[1]:,} tokens, ~${a[2]:.2f}). "
        "Estimates assume ~4 chars/token, no cache discount, $4/$20 per MTok."
    )
    return 0


# The `convert` command: print one file as markdown, so you can see what the rules will read.
def cmd_convert(args) -> int:
    """Print a file's markdown conversion; warnings and attachments go to stderr."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:  # never write into the input folder
        try:
            conv, _ = convert(Path(args.file), Path(tmp))
        except ConversionError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 1
    for w in conv.warnings:
        print(f"[warning] {w}", file=sys.stderr)
    for a in conv.attachments:
        print(f"[attachment] {a.name} (ingested as its own document by `ingest`)", file=sys.stderr)
    sys.stdout.write(conv.markdown)
    return 0


# The `pack` command: print the gbrain schema pack that the vocabulary generates.
def cmd_pack(args) -> int:
    """Print the gbrain schema pack generated from the vocabulary."""
    sys.stdout.write(Vocabulary().render_pack())
    return 0


# The `vocab` command: print the vocabulary reference as markdown. Fails if the vocabulary has
# mistakes.
def cmd_vocab(args) -> int:
    """Print the vocabulary reference (every verb and page type) as markdown."""
    vocab = Vocabulary()
    errors = vocab.problems()
    for e in errors:
        print(f"[vocabulary error] {e}", file=sys.stderr)
    sys.stdout.write(vocab.render_markdown())
    return 1 if errors else 0


# The `dashboard` command: rebuild the run-history page, in the default place or at --out.
def cmd_dashboard(args) -> int:
    """Rebuild the run-history page from the run history files."""
    _, work = _paths(args)
    out = Path(args.out).resolve() if args.out else work / "reports" / "dashboard.html"
    dashboard_page.write(work, out)
    log(f"Dashboard: {out}")
    return 0


# The `eval` command: score the pipeline against the answer keys. The tuned keys are the default;
# --holdout uses only the held-out keys, and --all uses both.
def cmd_eval(args) -> int:
    """Ingest each answer key's document into a throwaway brain and score every stage."""
    from docingest.evaluation import answer_key_evaluation

    _, work = _paths(args)
    root = answer_key_evaluation.DEFAULT_ANSWERS
    tuned, held_out = sorted(root.glob("*.yaml")), sorted((root / "holdout").glob("*.yaml"))
    # Answer key files named on the command line win. Otherwise pick the tuned, held-out or all
    # keys.
    keys = [Path(a) for a in args.answers] or (
        tuned + held_out if args.all else held_out if args.holdout else tuned
    )
    if not keys:
        sys.exit(f"No answer keys found in {answer_key_evaluation.DEFAULT_ANSWERS}")
    # Each key is ingested into a throwaway brain and scored. The brain is removed afterwards
    # unless --keep is given.
    reports = [answer_key_evaluation.run(k, work, keep=args.keep) for k in keys]
    md = answer_key_evaluation.write_reports(reports, work)
    run_history.append(work, "evals", [answer_key_evaluation.history_row(r) for r in reports])
    _write_dashboard(work)
    log(f"Evaluation report: {md}")
    return 0


# The `selftest` command. --update-baseline implies --full.
def cmd_selftest(args) -> int:
    """Did a change break anything? Quick rule tests; --full adds every test and the scores."""
    from docingest.evaluation import selftest

    _, work = _paths(args)
    return selftest.run(
        full=args.full or args.update_baseline, update_baseline=args.update_baseline, work=work
    )


# The `purge` command: soft-delete every page under a slug prefix. Without --yes it only shows what
# would go.
def cmd_purge(args) -> int:
    """Soft-delete every brain page whose slug starts with a prefix (dry run without --yes)."""
    # A very short prefix would match most of the brain, so refuse it.
    if len(args.prefix.strip("/ ")) < 3:
        sys.exit("Refusing to purge: the prefix is empty or too short and would match most pages")
    home, work = _paths(args)
    pipe = Pipeline(work, home)
    slugs = [
        line.split("\t")[0]
        for line in pipe.gb.run("list", "--limit", "1000000").stdout.splitlines()
        if line.startswith(args.prefix)
    ]
    if not slugs:
        log(f"No pages under {args.prefix}")
        return 0
    if not args.yes:
        log(
            f"{len(slugs)} page(s) under {args.prefix} would be soft-deleted "
            "(recoverable for 72 h). Re-run with --yes to delete."
        )
        return 0
    deleted = sum(pipe.gb.delete(s) for s in slugs)
    log(f"Soft-deleted {deleted}/{len(slugs)} page(s) under {args.prefix}")
    return 0 if deleted == len(slugs) else 1


# Print the headline result, then the documents and global checks that failed or warned. The full
# detail is in the report file.
def print_summary(report: dict, work: Path) -> None:
    """Log a one-line-per-document summary of a verification report."""
    s = report["summary"]
    log(
        f"Verification: {s['status'].upper()} — {s['documents']} document(s), {s['pages']} pages, "
        f"{s['chunks']} chunks, {s['links']} links ({s['seconds']} s)"
    )
    for d in report["documents"]:
        bad = [c for c in d["checks"] if c["status"] in ("fail", "warn")]
        log(
            f"  {d['status'].upper():4} {d['key']}"
            + (": " + ", ".join(f"{c['name']} {c['status']}" for c in bad) if bad else "")
        )
    for c in report["global"]["checks"]:
        if c["status"] in ("fail", "warn"):
            log(f"  {c['status'].upper():4} global {c['name']}: {c['detail']}")
    log(f"Report: {work / 'reports' / 'latest.md'}")


# Add the embedding options to a sub-command. Several commands share them.
def _embed_args(p) -> None:
    """Options shared by the commands that use embeddings."""
    p.add_argument(
        "--embedding-model", default=DEFAULT_MODEL, help="litellm:<model name on the proxy>"
    )
    p.add_argument("--embedding-dimensions", type=int, default=DEFAULT_DIMS)
    p.add_argument(
        "--max-chunks",
        type=int,
        default=20000,
        help="Refuse to embed more pending chunks than this",
    )
    p.add_argument(
        "--queries", help="YAML file of semantic-search checks (default: built-in samples)"
    )


# Define every command and its options, parse the command line, and run the chosen command.
# Returns the exit code.
def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the chosen command. Returns the exit code."""
    parser = argparse.ArgumentParser(prog="docingest", description="Ingest documents into gbrain")
    parser.add_argument(
        "--brain", help="Brain directory (GBRAIN_HOME); default from the environment"
    )
    parser.add_argument(
        "--work", help="Work directory for pages, manifests, state, reports (default ./work)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Each block from here defines one command: its options, and the function it runs.
    p = sub.add_parser("ingest", help="Ingest a file or every supported file under a directory")
    p.add_argument("path")
    p.add_argument(
        "--llm",
        action="store_true",
        help="Send low-confidence sections to Claude after rule extraction "
        "(needs LITELLM_BASE_URL and LITELLM_API_KEY)",
    )
    p.add_argument(
        "--llm-all",
        action="store_true",
        help="Send every section to Claude, not only low-confidence ones",
    )
    p.add_argument(
        "--llm-effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"]
    )
    p.add_argument(
        "--kind", help="Force the document kind (brd, prd, sow, ...) for top-level inputs"
    )
    p.add_argument("--no-verify", action="store_true", help="Skip post-ingestion verification")
    p.add_argument(
        "--no-eval", action="store_true", help="Skip scoring against answer keys after ingestion"
    )
    p.add_argument("--project", help="Project this run belongs to (or DOCINGEST_PROJECT)")
    p.add_argument("--team", help="Team that submitted the run (or DOCINGEST_TEAM)")
    p.add_argument(
        "--submitted-by", help="Who submitted the run (default DOCINGEST_USER, then the OS user)"
    )
    p.add_argument(
        "--embed",
        action="store_true",
        help="After verification passes, embed the chunks with the LiteLLM proxy and check them",
    )
    _embed_args(p)
    p.add_argument(
        "--search-checks",
        type=int,
        default=0,
        help="Max sections per document to search-check (0 = all)",
    )
    p.add_argument(
        "--schema-mode",
        default="auto",
        choices=["auto", "review", "off"],
        help="Schema gaps: auto = rules apply clear ones, the rest wait for a reviewer; "
        "review = every one waits; off = ignore them",
    )
    p.add_argument(
        "--pending-exit-code",
        type=int,
        default=2,
        help="Exit code when the run succeeded but proposals wait for a reviewer (0 = ignore)",
    )
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("embed", help="Embed pending chunks and run the embedding checks")
    _embed_args(p)
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser(
        "embeddings-setup",
        help="Check the proxy, then create or switch the brain to the embedding model",
    )
    _embed_args(p)
    p.set_defaults(func=cmd_embeddings_setup)

    p = sub.add_parser("doctor", help="Check that this machine is ready")
    _embed_args(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("verify", help="Re-run verification for ingested documents")
    p.add_argument("keys", nargs="*", help="Document keys (default: all)")
    p.add_argument("--search-checks", type=int, default=0)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser(
        "plan",
        help="Show which sections rules cover and which would go to the AI, with cost estimates",
    )
    p.add_argument("path")
    p.add_argument("--kind")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Also list high-confidence sections"
    )
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("convert", help="Print a file converted to markdown")
    p.add_argument("file")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("pack", help="Print the generated gbrain schema pack")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser(
        "vocab", help="Print every link verb and page type as markdown (docs/VOCABULARY.md)"
    )
    p.set_defaults(func=cmd_vocab)

    p = sub.add_parser("dashboard", help="Rebuild the run-history page (reports/dashboard.html)")
    p.add_argument("--out", help="Write the page here instead")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser(
        "eval",
        help="Score the pipeline against answer keys (default: answer_keys/*.yaml)",
    )
    p.add_argument("answers", nargs="*", help="Answer key files")
    p.add_argument("--holdout", action="store_true", help="Score only the held-out keys")
    p.add_argument("--all", action="store_true", help="Score the tuned and the held-out keys")
    p.add_argument("--keep", action="store_true", help="Keep the throwaway brains")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser(
        "selftest",
        help="Check that nothing broke: quick rule tests, or --full with scores vs the baseline",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="Also run every test and score all answer keys against answer_keys/baseline.json",
    )
    p.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write the current scores as the new baseline (implies --full; do it on purpose)",
    )
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("review", help="Review schema gaps: list, show, approve, map, reject, apply, revert")
    rsub = p.add_subparsers(dest="review_command", required=True)
    p.set_defaults(func=cmd_review, schema_mode="auto")

    # Options shared by the review decisions: who is deciding, why, and whether to skip the prompt.
    def _who(q):
        q.add_argument("--by", help="Reviewer name (default DOCINGEST_USER, then the OS user)")
        q.add_argument("--note", help="Why (required for a new type, a rejection, or going against the suggestion)")
        q.add_argument("--yes", action="store_true", help="Do not ask for confirmation")

    rsub.add_parser("list", help="Open proposals")
    q = rsub.add_parser("show", help="One proposal in full, with the preview of approving it")
    q.add_argument("id")
    q = rsub.add_parser("approve", help="Accept the suggestion (or --as <name> for a new type)")
    q.add_argument("id", nargs="?")
    q.add_argument("--as", dest="name", help="Name the new type this")
    q.add_argument("--all-suggested", action="store_true", help="Accept every suggestion that maps to an existing type or verb")
    _who(q)
    q = rsub.add_parser("map", help="Use an existing type (ID prefix) or verb (column)")
    q.add_argument("id")
    q.add_argument("target")
    _who(q)
    q = rsub.add_parser("reject", help="Leave it generic and never propose it again")
    q.add_argument("id")
    _who(q)
    rsub.add_parser("apply", help="Apply the decisions reviewers recorded, re-reading the documents they affect")
    q = rsub.add_parser("revert", help="Undo an applied decision")
    q.add_argument("id")
    q.add_argument("--by")
    q = rsub.add_parser("log", help="The audit trail")
    q.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("purge", help="Soft-delete every page under a slug prefix")
    p.add_argument("prefix")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_purge)

    # Parse the command line. The raw arguments are kept too, so the run history can record the
    # exact command.
    args = parser.parse_args(argv)
    args.argv = list(sys.argv[1:] if argv is None else argv)
    # One run ID per command: every log line, the run history and the recap share it.
    logmod.setup()
    logmod.set_run(run_history.new_run_id())
    log_cmd = logmod.get_logger(__name__)
    log_cmd.info("command started", command=args.command)
    started = time.time()
    try:
        code = args.func(args)
    except Exception:
        # Keep the traceback in the log file, then let the error reach the user as before.
        log_cmd.exception("command failed", command=args.command)
        raise
    log_cmd.step("command finished", command=args.command, secs=f"{time.time() - started:.1f}", exit=code)
    return code
