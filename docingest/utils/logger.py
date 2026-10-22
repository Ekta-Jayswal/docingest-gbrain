# What this file does: the one logging module for docingest. It defines the line format, the
# colors,
# the run and document IDs, and the log files. Every other file only calls get_logger() and passes
# a message plus attributes. Nothing else formats log lines or timestamps.
"""Logging for docingest.

A line looks like this:

  2026-10-22 20:13:04 IST INFO  [run=ab12cd doc=loan_origination_brd] docingest.ingestion.pipeline
  (pipeline.py:212) imported pages | pages=137 chunks=137 secs=2.1

Use it from any module:

    from docingest.utils.logger import get_logger
    log = get_logger(__name__)
    log.step("converted", file=path.name, secs=0.4)   # green: a key step completed
    log.info("routing", ai=11, rules=14)               # normal color
    log.warning("OCR needed", file=path.name)          # yellow
    log.error("model request failed", reason=str(e))   # red
    log.debug("table mapped", section=7)               # log file only

Console: INFO and above, colored only on a terminal (see NO_COLOR and DOCINGEST_COLOR).
Files: DEBUG and above, never colored. Each run also gets its own file, logs/runs/<run_id>.log.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from docingest.utils import clock

STEP = 25  # between INFO (20) and WARNING (30): printed like INFO, but shown green
logging.addLevelName(STEP, "STEP")

GREEN, YELLOW, RED, RESET = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[0m"
MAX_RUN_LOGS = 200  # per-run log files kept; older ones are deleted when a new run starts

_run_id: str | None = None
_doc_key: str = "-"
_factory_installed = False
_handlers_added: set[str] = set()


# Install a record factory that stamps every log record with the current run and document. It
# runs for every record, from every module, so no module has to pass the IDs itself.
def _install_factory() -> None:
    """Add the run and document IDs to every log record. Runs once."""
    global _factory_installed
    if _factory_installed:
        return
    previous = logging.getLogRecordFactory()

    # Stamp one record with the run and document IDs, then pass it on.
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.run = _run_id[-6:] if _run_id else "-"
        record.doc = _doc_key
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


# The run ID for this process, set once at the start of each command.
def set_run(run_id: str) -> None:
    """Make run_id the run ID on every line from now on."""
    global _run_id
    _run_id = run_id


# The current run ID, or None before a command has started.
def current_run() -> str | None:
    """The run ID set by set_run(), or None."""
    return _run_id


# Set the document key shown on each line. Used once the key of the document is known.
def set_document(key: str) -> None:
    """Make key the document ID on every line from now on."""
    global _doc_key
    _doc_key = key


# Show a document key while the code inside the block runs, then restore the previous one.
@contextlib.contextmanager
def document(key: str):
    """Context manager: lines inside the block carry this document key."""
    global _doc_key
    previous = _doc_key
    _doc_key = key
    try:
        yield
    finally:
        _doc_key = previous


# Formats one record: IST time, level, run and document IDs, module, file and line, and the
# message.
class _Formatter(logging.Formatter):
    """Line format with IST time; colored only when color=True."""

    # Remember whether to color the lines.
    def __init__(self, color: bool):
        super().__init__("%(message)s")
        self.color = color

    # The time of the record in IST, with the IST label.
    def formatTime(self, record, datefmt=None):  # noqa: N802 (logging API name)
        """The record's time in IST, such as 2026-10-22 20:13:04 IST."""
        moment = dt.datetime.fromtimestamp(record.created, clock.IST)
        return moment.strftime("%Y-%m-%d %H:%M:%S IST")

    # Build the full line, and wrap it in a color code when colors are on.
    def format(self, record):
        """Return one line: time, level, IDs, module, file, line and message."""
        line = (
            f"{self.formatTime(record)} {record.levelname:<5} "
            f"[run={getattr(record, 'run', '-')} doc={getattr(record, 'doc', '-')}] "
            f"{record.name} ({record.filename}:{record.lineno}) {record.getMessage()}"
        )
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if self.color:
            if record.levelno == STEP:
                return GREEN + line + RESET
            if record.levelno == logging.WARNING:
                return YELLOW + line + RESET
            if record.levelno >= logging.ERROR:
                return RED + line + RESET
        return line


# Decide whether to color the console: only on a terminal, unless the environment says otherwise.
def _use_color() -> bool:
    """True when the console should be colored.

    DOCINGEST_COLOR=always|never overrides; NO_COLOR turns color off.
    """
    choice = os.environ.get("DOCINGEST_COLOR", "auto").lower()
    if choice == "always":
        return True
    if choice == "never" or "NO_COLOR" in os.environ:
        return False
    return sys.stdout.isatty()


# Add the console handler (stdout) to the docingest logger. Safe to call more than once.
def setup() -> None:
    """Configure console logging. Level comes from DOCINGEST_LOG_LEVEL (default INFO)."""
    _install_factory()
    root = logging.getLogger("docingest")
    root.setLevel(logging.DEBUG)  # the handlers decide what each destination shows
    root.propagate = False
    if "console" not in _handlers_added:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_Formatter(color=_use_color()))
        handler.setLevel(os.environ.get("DOCINGEST_LOG_LEVEL", "INFO").upper())
        root.addHandler(handler)
        _handlers_added.add("console")


# Add the log files for this run under the work folder. Safe to call more than once.
def add_file(work: Path, run_id: str | None = None) -> None:
    """Write DEBUG logs to work/logs/docingest.log and work/logs/runs/<run_id>.log."""
    setup()
    logs = work / "logs"
    runs = logs / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("docingest")
    if "combined" not in _handlers_added:
        combined = logging.handlers.RotatingFileHandler(
            logs / "docingest.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        combined.setFormatter(_Formatter(color=False))
        combined.setLevel(logging.DEBUG)
        root.addHandler(combined)
        _handlers_added.add("combined")
    run_id = run_id or _run_id
    if run_id and f"run:{run_id}" not in _handlers_added:
        _prune(runs)
        per_run = logging.FileHandler(runs / f"{run_id}.log", encoding="utf-8")
        per_run.setFormatter(_Formatter(color=False))
        per_run.setLevel(logging.DEBUG)
        root.addHandler(per_run)
        _handlers_added.add(f"run:{run_id}")


# Delete the oldest per-run logs so that only the newest MAX_RUN_LOGS remain. Run IDs start with a
# time stamp, so sorting by name sorts by time.
def _prune(runs: Path) -> None:
    """Keep the newest MAX_RUN_LOGS per-run log files; delete the rest."""
    files = sorted(runs.glob("*.log"))
    for old in files[: max(0, len(files) - MAX_RUN_LOGS + 1)]:
        with contextlib.suppress(OSError):
            old.unlink()


# The logger handed to each module. Its methods take a message and optional attributes.
class _Log:
    """Thin wrapper over a standard logger: message plus key=value attributes."""

    # Keep the standard logger for this module.
    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    # Send one record. stacklevel=3 makes the file and line point at the caller, not at this file.
    def _emit(self, level: int, msg: str, fields: dict) -> None:
        """Format message | key=value pairs and log it at level."""
        text = msg
        if fields:
            text += " | " + " ".join(f"{k}={v}" for k, v in fields.items())
        self._log.log(level, text, stacklevel=3)

    # A key step completed (green on the console).
    def step(self, msg: str, **fields) -> None:
        """Log a key step that completed. Green on the console."""
        self._emit(STEP, msg, fields)

    # A normal progress line.
    def info(self, msg: str, **fields) -> None:
        """Log a normal line (INFO)."""
        self._emit(logging.INFO, msg, fields)

    # Something unexpected that did not stop the run (yellow on the console).
    def warning(self, msg: str, **fields) -> None:
        """Log a warning (yellow on the console)."""
        self._emit(logging.WARNING, msg, fields)

    # A failure (red on the console).
    def error(self, msg: str, **fields) -> None:
        """Log an error (red on the console)."""
        self._emit(logging.ERROR, msg, fields)

    # A failure with its traceback (red on the console; the traceback goes to the log files).
    def exception(self, msg: str, **fields) -> None:
        """Log an error together with the traceback of the exception being handled."""
        text = msg
        if fields:
            text += " | " + " ".join(f"{k}={v}" for k, v in fields.items())
        self._log.exception(text, stacklevel=2)

    # Detail for debugging: written to the log files only.
    def debug(self, msg: str, **fields) -> None:
        """Log detail that appears in the log files, not on the console."""
        self._emit(logging.DEBUG, msg, fields)


# Return a logger for one module. Use get_logger(__name__) at the top of the file.
def get_logger(name: str) -> _Log:
    """A logger for the module called name, with step, info, warning, error and debug methods."""
    _install_factory()
    return _Log(name)
