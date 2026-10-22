# What this file does: tests the one logging module. It checks the IST time, the run and document
# IDs, the colors, the caller's file and line, the per-run log file, and the LLM timeout setting.
"""Logging: IST time, run and document IDs, colors, and the log files."""

from __future__ import annotations

import datetime as dt
import io
import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from docingest.utils import clock
from docingest.utils import logger as logmod
from docingest.utils.llm_client import timeout_seconds


# Attach a handler that writes to a string, run the code, and return what was written.
def capture(fn) -> str:
    """Run fn with a string handler on the docingest logger; return the formatted output."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logmod._Formatter(color=False))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger("docingest")
    root.addHandler(handler)
    try:
        fn()
    finally:
        root.removeHandler(handler)
    return buf.getvalue()


# Shared setup and cleanup: reset the run and document IDs, and remove file handlers after each
# test.
class LoggerTest(unittest.TestCase):
    # Set known run and document IDs, and set up the console, before each test.
    def setUp(self):
        logmod.set_run("20261022T143400-abcdef")
        logmod.set_document("-")
        logmod.setup()
        self.log = logmod.get_logger("docingest.test")

    # Remove the file handlers and reset the IDs, so no test affects another.
    def tearDown(self):
        root = logging.getLogger("docingest")
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
                root.removeHandler(h)
        logmod._handlers_added.discard("combined")
        logmod._handlers_added = {h for h in logmod._handlers_added if not h.startswith("run:")}
        logmod.set_run("20261022T143400-abcdef")
        logmod.set_document("-")

    # A time given in UTC is shown in IST, with the IST label.
    def test_time_is_ist(self):
        record = logging.LogRecord("docingest.x", logging.INFO, __file__, 1, "m", None, None)
        record.created = dt.datetime(2026, 10, 22, 14, 34, 0, tzinfo=dt.timezone.utc).timestamp()
        self.assertTrue(logmod._Formatter(color=False).formatTime(record).startswith("2026-10-22 20:04:00 IST"))
        self.assertEqual(clock.now().utcoffset(), dt.timedelta(hours=5, minutes=30))

    # Every line carries the run ID's last six characters and the current document key.
    def test_run_and_document_ids_on_each_line(self):
        # Write two lines: one without a document key, one inside a document block.
        def write():
            self.log.info("hello")
            with logmod.document("loan_origination_brd"):
                self.log.info("inside")
        out = capture(write).splitlines()
        self.assertIn("[run=abcdef doc=-]", out[0])
        self.assertIn("[run=abcdef doc=loan_origination_brd]", out[1])
        self.assertNotIn("doc=-", out[1])

    # After the document block ends, the previous key is back.
    def test_document_block_restores_previous_key(self):
        logmod.set_document("outer")
        with logmod.document("inner"):
            self.assertEqual(logmod._doc_key, "inner")
        self.assertEqual(logmod._doc_key, "outer")

    # The line shows the module and the file and line of the caller, not of the logger module.
    def test_caller_file_and_line(self):
        out = capture(lambda: self.log.info("where"))
        self.assertIn("test_logger.py:", out)
        self.assertIn("(test_logger.py:", out)

    # Key steps are green, warnings yellow, errors red; plain lines have no color codes.
    def test_colors(self):
        fmt = logmod._Formatter(color=True)

        # Build a bare log record at the given level, as the formatter would see it.
        def make(level):
            return logging.LogRecord("docingest.c", level, __file__, 1, "m", None, None)

        self.assertTrue(fmt.format(make(logmod.STEP)).startswith(logmod.GREEN))
        self.assertTrue(fmt.format(make(logging.WARNING)).startswith(logmod.YELLOW))
        self.assertTrue(fmt.format(make(logging.ERROR)).startswith(logmod.RED))
        self.assertNotIn("\x1b[", fmt.format(make(logging.INFO)))
        self.assertNotIn("\x1b[", logmod._Formatter(color=False).format(make(logmod.STEP)))

    # Attributes follow the message as key=value pairs.
    def test_attributes_are_appended(self):
        out = capture(lambda: self.log.step("converted", file="brd.md", secs="0.4"))
        self.assertIn("converted | file=brd.md secs=0.4", out)

    # The console level is INFO by default, so debug lines are kept out of the terminal.
    def test_console_level_and_setup_idempotent(self):
        root = logging.getLogger("docingest")
        consoles = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        logmod.setup()
        logmod.setup()
        after = [h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        self.assertEqual(len(consoles), len(after))
        self.assertEqual(after[0].level, logging.INFO)

    # Each run gets its own log file with its DEBUG lines; the console does not get them.
    def test_per_run_file_holds_debug_lines(self):
        work = Path(tempfile.mkdtemp())
        try:
            logmod.add_file(work, "20261022T150000-111111")
            logmod.set_run("20261022T150000-111111")
            self.log.debug("detail only in the file")
            text = (work / "logs" / "runs" / "20261022T150000-111111.log").read_text(encoding="utf-8")
            self.assertIn("detail only in the file", text)
            self.assertIn("[run=111111", text)
            self.assertNotIn("\x1b[", text)
            self.assertTrue((work / "logs" / "docingest.log").exists())
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # The dashboard's run row links to the run's log file, relative to where the dashboard is
    # saved.
    def test_dashboard_links_each_run_to_its_log(self):
        from docingest.reporting import dashboard_page, run_history

        work = Path(tempfile.mkdtemp())
        try:
            run_id = "20261022T150000-111111"
            # The link only appears when the log file exists.
            (work / "logs" / "runs").mkdir(parents=True)
            (work / "logs" / "runs" / f"{run_id}.log").write_text("log")
            run_history.append(work, "runs", [{"run_id": run_id, "started_at": "2026-10-22T15:00:00+0530",
                                               "log_file": f"logs/runs/{run_id}.log", "documents": 1}])
            default = dashboard_page.render(work)
            self.assertIn(f'href="../logs/runs/{run_id}.log"', default)
            nested = dashboard_page.render(work, work / "reports" / "sub" / "dashboard.html")
            self.assertIn(f'href="../../logs/runs/{run_id}.log"', nested)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # The dashboard only carries a Review button; the proposals are on the review page written
    # next to it.
    def test_dashboard_has_review_button_and_separate_page(self):
        from docingest.reporting import dashboard_page

        work = Path(tempfile.mkdtemp())
        try:
            out = work / "reports" / "dashboard.html"
            dashboard_page.write(work, out)
            self.assertIn('href="review.html"', out.read_text())
            review = (out.parent / "review.html").read_text()
            self.assertIn("Nothing is waiting for a reviewer", review)
            self.assertIn('href="dashboard.html"', review)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # The LLM timeout comes from LLM_TIMEOUT, and defaults to 300 seconds.
    def test_llm_timeout_setting(self):
        saved = os.environ.pop("LLM_TIMEOUT", None)
        try:
            self.assertEqual(timeout_seconds(), 300)
            os.environ["LLM_TIMEOUT"] = "120"
            self.assertEqual(timeout_seconds(), 120)
        finally:
            os.environ.pop("LLM_TIMEOUT", None)
            if saved is not None:
                os.environ["LLM_TIMEOUT"] = saved


if __name__ == "__main__":
    unittest.main()
