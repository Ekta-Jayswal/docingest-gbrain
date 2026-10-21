"""Command line: python -m docingest <command> ..."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import llm
from .classify import classify
from .confidence import estimate, route_sections
from .convert import ConversionError, convert
from .extract import Extractor, document_id
from .mdutil import slugify
from .pipeline import Pipeline, collect_inputs, log
from .vocab import Vocabulary


def _paths(args) -> tuple[Path, Path]:
    home = args.brain or os.environ.get("GBRAIN_HOME")
    if not home:
        sys.exit("Set GBRAIN_HOME or pass --brain <dir> (the brain lives in <dir>/.gbrain)")
    work = Path(args.work or os.environ.get("DOCINGEST_WORK") or Path.cwd() / "work").resolve()
    return Path(home).resolve(), work


def cmd_ingest(args) -> int:
    """Ingest a file or folder, then verify it. Returns the process exit code."""
    home, work = _paths(args)
    if args.llm_all:
        args.llm = True
    if args.llm and not llm.credentials_available():
        sys.exit("--llm needs ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) in the environment")
    source = Path(args.path).resolve()
    if not source.exists():
        sys.exit(f"No such file or directory: {source}")
    inputs, skipped = collect_inputs(source, exclude=[work, home])
    for s in skipped:
        log(f"Skipping unsupported file: {s}")
    if not inputs:
        sys.exit(f"No supported documents found under {source}")
    log(f"{len(inputs)} document(s) to ingest into {home}")

    pipe = Pipeline(
        work,
        home,
        use_llm=args.llm,
        llm_effort=args.llm_effort,
        kind=args.kind,
        llm_mode="all" if args.llm_all else "auto",
    )
    result = pipe.ingest(inputs)
    exit_code = 1 if result["failed"] else 0
    for f in result["failed"]:
        log(f"Failed: {f['file']}: {f['error']}")

    if not args.no_verify and result["processed"]:
        log("Verifying ingestion ...")
        report = pipe.verify(result["processed"], args.search_checks)
        print_summary(report, work)
        if report["summary"]["status"] == "fail":
            exit_code = 1
    return exit_code


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


def cmd_plan(args) -> int:
    """Show per-section rule confidence and AI cost estimates, without touching the brain."""
    import tempfile

    source = Path(args.path).resolve()
    inputs, _ = collect_inputs(source, exclude=[])
    vocab = Vocabulary()
    system_chars = len(llm._system_prompt(vocab)) + len(str(llm._schema(vocab)))
    totals = {"hybrid": [0, 0, 0.0], "all_sections": [0, 0, 0.0]}
    with tempfile.TemporaryDirectory() as tmp:
        for path in inputs:
            try:
                conv, reference = convert(path, Path(tmp))
            except ConversionError as e:
                print(f"\n{path.name}: conversion failed ({e})")
                continue
            kind = classify(conv, vocab, args.kind)
            key = slugify(document_id(conv.markdown) or path.stem, 50)
            x = Extractor(vocab)
            res = x.extract(path, conv, reference, kind, key, "")
            routing = route_sections(res, list(x._globals.values()))
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


def cmd_pack(args) -> int:
    """Print the gbrain schema pack generated from the vocabulary."""
    sys.stdout.write(Vocabulary().render_pack())
    return 0


def cmd_purge(args) -> int:
    """Soft-delete every brain page whose slug starts with a prefix (dry run without --yes)."""
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

    p = sub.add_parser("ingest", help="Ingest a file or every supported file under a directory")
    p.add_argument("path")
    p.add_argument(
        "--llm",
        action="store_true",
        help="Send low-confidence sections to Claude after rule extraction "
        "(needs ANTHROPIC_API_KEY)",
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
        "--search-checks",
        type=int,
        default=0,
        help="Max sections per document to search-check (0 = all)",
    )
    p.set_defaults(func=cmd_ingest)

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

    p = sub.add_parser("purge", help="Soft-delete every page under a slug prefix")
    p.add_argument("prefix")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    return args.func(args)
