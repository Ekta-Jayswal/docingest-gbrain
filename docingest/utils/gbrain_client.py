# What this file does: talks to gbrain, the brain that stores the ingested pages.
# GBrain runs gbrain CLI commands for setup and writes. Reader keeps one MCP session open
# so many reads can run quickly.

"""gbrain access: CLI for writes, a long-lived MCP session for fast reads."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from docingest.utils.logger import get_logger

_log = get_logger(__name__)

# Verb names the pipeline used before the rename. Their links are removed from the brain.
RETIRED_VERBS = ("achieves", "mentions_item")


# Raised when a gbrain command or MCP call fails, so callers catch one error type.
class GBrainError(Exception):
    """A gbrain command or MCP call failed."""


# Wrapper around the gbrain command-line tool. Every command runs against one brain
# folder (self.home).
class GBrain:
    """Runs gbrain CLI commands against one brain directory."""

    # home is the brain folder. binary is the gbrain program; log_dir is where command
    # output is logged.
    def __init__(self, home: Path, binary: str | None = None, log_dir: Path | None = None):
        self.home = home
        # Find the gbrain program. Order: the binary argument, the GBRAIN_BIN variable, the PATH,
        # then the default Bun install location.
        self.binary = (
            binary
            or os.environ.get("GBRAIN_BIN")
            or shutil.which("gbrain")
            or str(Path.home() / ".bun/bin/gbrain")
        )
        self.log_dir = log_dir
        # Environment for every gbrain call. GBRAIN_HOME points gbrain at this brain, and the
        # folder of the gbrain program is added to PATH.
        self.env = dict(
            os.environ,
            GBRAIN_HOME=str(home),
            GBRAIN_NO_AUTOPILOT_INSTALL="1",
            PATH=f"{Path(self.binary).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        )

    # Folder to run gbrain from: the brain folder if it exists, otherwise nothing.
    def _cwd(self) -> str | None:
        """Run gbrain from inside the brain folder.

        gbrain (through Bun) reads ``.env`` and ``.env.local`` in its working directory and then
        ignores ``GBRAIN_HOME``, which would send commands to a different brain.
        """
        return str(self.home) if self.home.is_dir() else None

    # Run ``gbrain <args>`` and return the finished process. Logs the output, and raises
    # GBrainError on failure when check is True.
    def run(
        self, *args: str, check: bool = True, timeout: int = 1800
    ) -> subprocess.CompletedProcess:
        """Run ``gbrain <args>``, log it, and raise GBrainError on failure when ``check``."""
        started = time.time()
        proc = subprocess.run(
            [self.binary, *args],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self._cwd(),
            timeout=timeout,
            check=False,
        )  # exit code handled below
        _log.debug("gbrain call", args=" ".join(args[:3]), exit=proc.returncode,
                   secs=f"{time.time() - started:.2f}")
        # gbrain prints this when it would ignore GBRAIN_HOME. Stop here so nothing is written
        # to a different brain.
        if "Ignoring GBRAIN_HOME" in proc.stderr:
            raise GBrainError(
                "gbrain ignored GBRAIN_HOME and would have used another brain. "
                f"Details: {proc.stderr.strip()[:500]}"
            )
        # Append the command and the end of its output to the log, for debugging later.
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / "gbrain-cli.log", "a") as log:
                log.write(
                    f"$ gbrain {' '.join(args)}\n[exit {proc.returncode}]\n{proc.stderr[-4000:]}\n"
                )
        # Non-zero exit with check on: raise an error that includes the end of stderr, which
        # usually gives the reason.
        if check and proc.returncode != 0:
            raise GBrainError(
                f"gbrain {' '.join(args)} failed ({proc.returncode}): {proc.stderr[-1500:]}"
            )
        return proc

    # Run a gbrain command and return the JSON it printed. Used by commands that support --json.
    def run_json(self, *args: str):
        """Run a gbrain command and parse the JSON in its output (ignoring trailing notices)."""
        out = self.run(*args).stdout
        # The JSON starts at the first { or [ in the output. Anything before it is notices or logs.
        start = min((i for i in (out.find("{"), out.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise GBrainError(f"gbrain {' '.join(args)} returned no JSON")
        # Decode one JSON value and ignore any text after it.
        value, _ = json.JSONDecoder().raw_decode(out[start:])
        return value

    # ---- setup ---------------------------------------------------------------

    # Check whether the brain folder sits inside a git repository (such as this project's clone).
    def _in_git_worktree(self) -> bool:
        """True if the brain folder is inside a git repository (for example the project clone)."""
        # The brain folder may not exist yet, so walk up to the nearest folder that does.
        # git needs a real folder to ask.
        probe = self.home
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            out = subprocess.run(
                ["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:  # git is not installed
            return False
        return out.stdout.strip() == "true"

    # A brain exists once gbrain has written its config file in the home folder.
    def exists(self) -> bool:
        """True if a brain has been created in this home directory."""
        return (self.home / ".gbrain" / "config.json").exists()

    # Create the brain if it does not exist yet (first-run setup). Returns True if it created one.
    def ensure_initialized(self, embedding: tuple[str, int] | None = None) -> bool:
        """Create a PGLite brain if none exists. True if created.

        Without ``embedding`` the brain is keyless with embeddings off. With
        ``(model, dimensions)`` the vector column is created at that width. The width cannot
        be changed cheaply later, so it is chosen here.
        """
        if self.exists():
            return False
        self.home.mkdir(parents=True, exist_ok=True)
        # gbrain refuses to put its content folder inside someone else's git repository
        # (such as this project's clone) unless it may create a repository of its own there.
        git = ["--git"] if self._in_git_worktree() else []
        # With a model given, create the brain with vector search at that model's width.
        if embedding:
            model, dims = embedding
            self.run(
                "init", "--pglite", "--non-interactive", *git,
                "--embedding-model", model, "--embedding-dimensions", str(dims),
            )  # fmt: skip
        else:
            self.run("init", "--pglite", "--no-embedding", "--non-interactive", *git)
        if not self.exists():
            raise GBrainError(f"gbrain init did not create a brain in {self.home}")
        return True

    # Read the brain's config file. Returns an empty dict if it is missing or unreadable.
    def config(self) -> dict:
        """The brain's gbrain config (``<home>/.gbrain/config.json``), or {} if absent."""
        try:
            return json.loads((self.home / ".gbrain" / "config.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    # Embeddings count as on only when a model is set and the brain has not turned them off.
    def embeddings_enabled(self) -> bool:
        """True once ``embeddings enable`` has switched embeddings on for this brain."""
        cfg = self.config()
        return not cfg.get("embedding_disabled", False) and bool(cfg.get("embedding_model"))

    # Create vectors for every chunk that does not have one yet. Returns gbrain's summary.
    def embed_stale(self) -> dict:
        """Embed every chunk that has no vector yet.

        ``--yes`` is used instead of ``--max-usd`` because gbrain has no price for LiteLLM
        models and refuses any ``--max-usd`` run. The caller limits the chunk count instead.
        """
        return self.run_json("embed", "--stale", "--yes", "--json")

    # Ask gbrain's embedding health check how many chunks are embedded and how many are
    # still pending. Returns the status plus those counts.
    def embedding_state(self) -> dict:
        """The ``embedding_state`` doctor check: counts of embedded and pending chunks."""
        doc = self.run_json("doctor", "--json", "--only", "embedding_state")
        checks = doc.get("checks") or [{}]
        return {
            "status": checks[0].get("status", "unknown"),
            "message": checks[0].get("message", ""),
            **(checks[0].get("details") or {}),
        }

    # Install a schema pack (the page types and link rules the brain enforces).
    # Steps: write the YAML, validate it, lint it, then switch the brain to use it.
    def install_pack(self, name: str, yaml_text: str) -> None:
        """Write, validate, lint and activate a schema pack. Lint errors raise GBrainError."""
        pack_dir = self.home / ".gbrain" / "schema-packs" / name
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "pack.yaml").write_text(yaml_text)
        self.run("schema", "validate", name)
        # gbrain reports lint problems as text lines that start with ERROR. Collect those and
        # fail if there are any.
        lint = self.run("schema", "lint", name, check=False)
        errors = [
            line.strip()
            for line in (lint.stdout + lint.stderr).splitlines()
            if line.strip().startswith("ERROR")
        ]
        if errors:
            raise GBrainError("Schema pack lint errors:\n" + "\n".join(errors))
        self.run("schema", "use", name)

    # ---- writes --------------------------------------------------------------

    # Import a folder of markdown pages into the brain. Embeddings are skipped here to keep
    # the import fast.
    def import_dir(self, path: Path) -> dict:
        """Import a directory of markdown pages without embeddings; returns gbrain's summary."""
        return self.run_json("import", str(path), "--no-embed", "--fresh", "--json")

    # Build graph links from the link fields in each page's frontmatter (the header block at
    # the top of a page).
    def extract_links(self) -> dict:
        """Turn frontmatter link fields into typed graph edges for the whole brain."""
        return self.run_json(
            "extract", "links", "--source", "db", "--include-frontmatter", "--json"
        )

    # Remove outgoing links that use a verb name the pipeline has since renamed. Links are only
    # added, so a brain built before the rename keeps the old names. Returns how many were removed.
    def drop_retired_links(self, reader: Reader, slugs: list[str]) -> int:
        """Unlink each page's outgoing edges that use a retired verb name."""
        dropped = 0
        for slug in slugs:
            for link in reader.links(slug):
                if link.get("from_slug") == slug and link.get("link_type") in RETIRED_VERBS:
                    self.run(
                        "unlink", slug, link["to_slug"], "--link-type", link["link_type"], check=False
                    )
                    dropped += 1
        return dropped

    # Fill in missing page types from the active schema pack.
    def schema_sync(self) -> None:
        """Backfill page types from the active schema pack."""
        self.run("schema", "sync", "--apply")

    # Soft-delete a page: it can be recovered for 72 hours. Returns True if it worked.
    def delete(self, slug: str) -> bool:
        """Soft-delete a page (recoverable for 72 hours). True on success."""
        return self.run("delete", slug, "--force", check=False).returncode == 0

    # Per-type page counts from gbrain, as JSON.
    def schema_stats(self) -> dict:
        """Per-type page counts and typed coverage, as JSON."""
        return self.run_json("schema", "stats", "--json")

    # gbrain's stats summary as plain text.
    def stats_text(self) -> str:
        """``gbrain stats`` output as text."""
        return self.run("stats").stdout


# Read side of the brain. One gbrain MCP server runs in a background thread, so reads reuse
# it instead of starting gbrain again for every call.
class Reader:
    """Synchronous facade over one `gbrain serve` MCP stdio session (~0.1 s per call)."""

    # Prepare the reader. Nothing starts until it is entered with a 'with' block.
    def __init__(self, gbrain: GBrain):
        self.gb = gbrain
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._queue: asyncio.Queue | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    # Start the background thread and wait until the MCP session is ready (or has failed).
    def __enter__(self) -> Reader:
        self._thread.start()
        self._ready.wait(120)
        if self._error:
            raise GBrainError(f"Could not start gbrain MCP server: {self._error}")
        if not self._ready.is_set():
            raise GBrainError("gbrain MCP server did not start within 120 s")
        return self

    # Stop the session: send the end marker (None) to the worker, then wait for its thread.
    def __exit__(self, *exc) -> None:
        if self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        self._thread.join(30)

    # Thread body: run the async worker on its own event loop. A startup error is kept so
    # __enter__ can raise it.
    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker())
        except BaseException as e:  # surfaced to the caller via _error
            self._error = e
            self._ready.set()

    # Open the gbrain MCP server over stdio, then run queued tool calls one at a time until
    # the end marker arrives.
    async def _worker(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.gb.binary,
            args=["serve", "--surface", "full"],
            env=self.gb.env,
            cwd=self.gb._cwd(),
        )
        log_file = self.gb.log_dir / "gbrain-serve.log" if self.gb.log_dir else Path(os.devnull)
        with open(log_file, "a") as errlog:
            async with (
                stdio_client(params, errlog=errlog) as (r, w),
                ClientSession(r, w) as session,
            ):
                await session.initialize()
                self._queue = asyncio.Queue()
                self._ready.set()
                # Take each queued request (tool, args, future), run it, and hand the result back
                # to the caller that is waiting on the future.
                while (item := await self._queue.get()) is not None:
                    tool, args, fut = item
                    try:
                        fut.set_result(await session.call_tool(tool, args))
                    except Exception as e:  # returned to the waiting caller
                        fut.set_exception(e)

    # Call one MCP tool from any thread and wait for its result. Returns the parsed JSON, or
    # the raw text if the reply is not JSON.
    def call(self, tool: str, args: dict, timeout: int = 120):
        """Call one MCP tool and return its parsed JSON result (or raw text)."""
        if self._queue is None:
            raise GBrainError("Reader is not started; use it as a context manager")
        # Hand the request to the worker thread. The future is where its result comes back.
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (tool, args, fut))
        res = fut.result(timeout)
        text = "".join(getattr(c, "text", "") for c in res.content)
        # The server flags a failed call with an error flag. Turn that into an exception that
        # includes the start of the message.
        if getattr(res, "is_error", None) or getattr(res, "isError", None):
            raise GBrainError(f"{tool} {args}: {text[:500]}")
        # The server may append a plain-text notice after the JSON payload.
        try:
            value, _ = json.JSONDecoder().raw_decode(text.lstrip())
            return value
        except json.JSONDecodeError:
            return text

    # ---- typed helpers -----------------------------------------------------------

    # Return a page as gbrain stores it, or None if it does not exist.
    def page(self, slug: str) -> dict | None:
        """A page as stored by gbrain, or None if it does not exist."""
        try:
            return self.call("get_page", {"slug": slug})
        except GBrainError as e:
            if "not_found" in str(e) or "not found" in str(e).lower():
                return None
            raise

    # Return every chunk of a page. gbrain sends chunks in batches, so keep asking for the
    # next batch until it says there are no more.
    def chunks(self, slug: str) -> list[dict]:
        """Every chunk of a page, following gbrain's paging."""
        out, start = [], 0
        while True:
            batch = self.call("get_chunks", {"slug": slug, "from_index": start, "limit": 100})
            if not isinstance(batch, list) or not batch:
                return out
            out.extend(batch)
            nxt = batch[-1].get("next_from_index")
            if not batch[-1].get("truncated") or nxt is None:
                return out
            start = nxt

    # Hybrid search: matches on keywords and on meaning. The results may be wrapped in a dict,
    # so unwrap them to a list.
    def query(self, text: str, limit: int = 10) -> list[dict]:
        """Hybrid (keyword + semantic) search."""
        res = self.call("query", {"query": text, "limit": limit}, timeout=300)
        if isinstance(res, dict):
            res = res.get("results", [])
        return res if isinstance(res, list) else []

    # Links going out of a page and links coming into it.
    def links(self, slug: str) -> list[dict]:
        """Outgoing and incoming links of a page."""
        res = self.call("get_links", {"slug": slug})
        return res if isinstance(res, list) else []

    # Keyword search only (no meaning match). types optionally limits results to page types.
    def search(self, query: str, types: list[str] | None = None, limit: int = 50) -> list[dict]:
        """Keyword search, optionally limited to page types."""
        args = {"query": query, "limit": limit, "match": "keyword"}
        if types:
            args["types"] = types
        res = self.call("search", args)
        if isinstance(res, dict):
            res = res.get("results", [])
        return res if isinstance(res, list) else []
