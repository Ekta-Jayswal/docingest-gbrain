# What this file does: the `selftest` command. The quick run executes the rule unit tests.
# The full run also scores every answer key and compares the scores with the saved baseline,
# so a change that makes extraction worse is caught before it ships.

"""Self-test: did a change break anything?

``selftest`` (quick, seconds) runs the rule unit tests. ``selftest --full`` also runs the
slower tests (they ingest the sample documents into a throwaway brain) and scores every
answer key, tuned and held out, against ``answer_keys/baseline.json``. It fails when a
stage score drops by more than a point or when precision drops at all (a wrong entity or
link appeared). ``--update-baseline`` writes the current scores as the new baseline: do
it on purpose, after a deliberate improvement or a reviewed change to an answer key.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

from docingest.evaluation import answer_key_evaluation as ake

# The project root folder: two levels above this file.
ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
BASELINE = ROOT / "answer_keys" / "baseline.json"
# Unit-test modules for the quick run. They run in seconds and do not ingest any documents.
QUICK_MODULES = [
    "tests.test_markdown_parsing",
    "tests.test_vocabulary",
    "tests.test_list_extraction",
    "tests.test_table_extraction",
    "tests.test_selftest",
    "tests.test_pdf_conversion",
]
TOLERANCE = 0.01  # a stage may lose up to a point (rounding of small answer keys)
# The scores compared against the baseline: each stage plus the overall score.
CHECKED = [*ake.OVERALL, "overall"]
PRECISION = ["entities_precision", "links_precision"]  # any drop means something wrong appeared


# Every answer key file: the tuned ones first, then the held-out ones.
def answer_keys() -> list[Path]:
    """Every answer key: the tuned ones, then the held-out ones."""
    return sorted(ake.DEFAULT_ANSWERS.glob("*.yaml")) + sorted(
        (ake.DEFAULT_ANSWERS / "holdout").glob("*.yaml")
    )


# Format a score as a percentage with one decimal place, or '-' when there is no score.
def _pct(v) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


# Compare the current scores with the baseline. Returns (problems, improvements).
def compare(rows: dict[str, dict], baseline: dict[str, dict]) -> tuple[list[str], list[str]]:
    """(problems, improvements) of the current scores against the baseline."""
    problems, improved = [], []
    for name, row in rows.items():
        base = baseline.get(name)
        if base is None:
            problems.append(
                f"{name}: no baseline yet (after review, run: selftest --update-baseline)"
            )
            continue
        for k in CHECKED:
            before, now = base.get(k) or 0.0, row.get(k) or 0.0
            # A drop bigger than the tolerance is a problem. A rise bigger than it is an
            # improvement.
            if before - now > TOLERANCE:
                problems.append(f"{name}: {k} fell from {_pct(before)} to {_pct(now)}")
            elif now - before > TOLERANCE:
                improved.append(f"{name}: {k} rose from {_pct(before)} to {_pct(now)}")
        # Precision must never drop, even slightly, because a drop means a wrong entity or link
        # appeared.
        for k in PRECISION:
            before, now = base.get(k), row.get(k)
            if before is not None and (now is None or now < before - 1e-9):
                problems.append(f"{name}: {k} fell from {_pct(before)} to {_pct(now)}")
    # An answer key that is in the baseline but was not run counts as a problem.
    for name in baseline.keys() - rows.keys():
        problems.append(f"{name}: in the baseline but its answer key was not run")
    return problems, improved


# Pick the scores that are compared with the baseline out of one answer key's report.
def _row(report: dict) -> dict:
    h = ake.history_row(report)
    return {k: h.get(k) for k in [*CHECKED, *PRECISION]}


# Run the unit tests and return the result. Quick runs only the listed modules; full runs
# every test under tests/.
def run_unit_tests(full: bool) -> unittest.result.TestResult:
    """The quick rule tests, or every test (some ingest the samples into a throwaway brain)."""
    if not TESTS.is_dir():
        sys.exit(f"{TESTS} not found: the tests are not installed here (use an image that ships tests/)")
    sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    suite = (
        loader.discover(str(TESTS), top_level_dir=str(ROOT))
        if full
        else loader.loadTestsFromNames(QUICK_MODULES)
    )
    return unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)


# Run the self-test and write its report. Returns 0 only when everything passes.
def run(full: bool, update_baseline: bool, work: Path) -> int:
    """Run the self-test; the exit code is 0 only when everything passes."""
    started = time.time()
    result = run_unit_tests(full)
    tests_ok = result.wasSuccessful()
    lines = [f"Unit tests: {result.testsRun} run, "
             f"{len(result.failures) + len(result.errors)} failed ({'full' if full else 'quick'})"]  # fmt: skip
    problems: list[str] = []
    if not tests_ok:
        problems.append("unit tests failed (details above)")

    if full:
        # Score every answer key, tuned and held out.
        reports = [ake.run(k, work) for k in answer_keys()]
        rows = {r["answer_key"]: _row(r) for r in reports}
        # Save the current scores as the new baseline. Refused while any unit test fails.
        if update_baseline:
            if not tests_ok:
                sys.exit("Not updating the baseline while tests fail")
            BASELINE.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
            lines.append(f"Baseline written: {BASELINE} ({len(rows)} answer keys)")
        else:
            if not BASELINE.exists():
                problems.append(f"no baseline at {BASELINE} (run: selftest --update-baseline)")
                baseline = {}
            else:
                baseline = json.loads(BASELINE.read_text())
            # Compare with the saved baseline, if there is one. Without a baseline, that is a
            # problem.
            found, improved = compare(rows, baseline)
            problems += found
            lines += ["", "| Answer key | Overall | Baseline | Result |", "|---|---|---|---|"]
            for name, row in rows.items():
                base = baseline.get(name, {}).get("overall")
                bad = any(p.startswith(f"{name}:") for p in found)
                lines.append(
                    f"| {name} | {_pct(row['overall'])} | {_pct(base)} | {'FAIL' if bad else 'ok'} |"
                )
            if improved:
                lines += ["", "Improved (raise the bar with --update-baseline once reviewed):"]
                lines += [f"- {i}" for i in improved]
    else:
        lines.append("Quick run only: add --full to score the answer keys against the baseline.")

    lines += ["", f"{'FAIL' if problems else 'PASS'} in {time.time() - started:.0f}s"]
    if problems:
        lines += ["", "Problems:", *[f"- {p}" for p in problems]]
    text = "\n".join(lines) + "\n"
    # The report is saved in the work folder as well as printed to the screen.
    out = work / "reports" / "selftest-latest.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# docingest self-test\n\n" + text)
    print(text)
    print(f"Report: {out}")
    return 1 if problems else 0
