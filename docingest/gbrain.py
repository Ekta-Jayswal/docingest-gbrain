"""gbrain access: CLI for writes, a long-lived MCP session for fast reads."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path


class GBrainError(Exception):
    """A gbrain command or MCP call failed."""


class GBrain:
    """Runs gbrain CLI commands against one brain directory."""

    def __init__(self, home: Path, binary: str | None = None, log_dir: Path | None = None):
        self.home = home
        self.binary = (
            binary
            or os.environ.get("GBRAIN_BIN")
            or shutil.which("gbrain")
            or str(Path.home() / ".bun/bin/gbrain")
        )
        self.log_dir = log_dir
        self.env = dict(
            os.environ,
            GBRAIN_HOME=str(home),
            GBRAIN_NO_AUTOPILOT_INSTALL="1",
            PATH=f"{Path(self.binary).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        )

    def _cwd(self) -> str | None:
        """Run gbrain from inside the brain folder.

        gbrain (through Bun) reads ``.env`` and ``.env.local`` in its working directory and then
        ignores ``GBRAIN_HOME``, which would send commands to a different brain.
        """
        return str(self.home) if self.home.is_dir() else None

    def run(
        self, *args: str, check: bool = True, timeout: int = 1800
    ) -> subprocess.CompletedProcess:
        """Run ``gbrain <args>``, log it, and raise GBrainError on failure when ``check``."""
        proc = subprocess.run(
            [self.binary, *args],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self._cwd(),
            timeout=timeout,
            check=False,
        )  # exit code handled below
        if "Ignoring GBRAIN_HOME" in proc.stderr:
            raise GBrainError(
                "gbrain ignored GBRAIN_HOME and would have used another brain. "
                f"Details: {proc.stderr.strip()[:500]}"
            )
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / "gbrain-cli.log", "a") as log:
                log.write(
                    f"$ gbrain {' '.join(args)}\n[exit {proc.returncode}]\n{proc.stderr[-4000:]}\n"
                )
        if check and proc.returncode != 0:
            raise GBrainError(
                f"gbrain {' '.join(args)} failed ({proc.returncode}): {proc.stderr[-1500:]}"
            )
        return proc

    def run_json(self, *args: str):
        """Run a gbrain command and parse the JSON in its output (ignoring trailing notices)."""
        out = self.run(*args).stdout
        start = min((i for i in (out.find("{"), out.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise GBrainError(f"gbrain {' '.join(args)} returned no JSON")
        value, _ = json.JSONDecoder().raw_decode(out[start:])
        return value

    # ---- setup ---------------------------------------------------------------

    def _in_git_worktree(self) -> bool:
        """True if the brain folder is inside a git repository (for example the project clone)."""
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

    def exists(self) -> bool:
        """True if a brain has been created in this home directory."""
        return (self.home / ".gbrain" / "config.json").exists()

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

    def config(self) -> dict:
        """The brain's gbrain config (``<home>/.gbrain/config.json``), or {} if absent."""
        try:
            return json.loads((self.home / ".gbrain" / "config.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def embeddings_enabled(self) -> bool:
        """True once ``embeddings enable`` has switched embeddings on for this brain."""
        cfg = self.config()
        return not cfg.get("embedding_disabled", False) and bool(cfg.get("embedding_model"))

    def embed_stale(self) -> dict:
        """Embed every chunk that has no vector yet.

        ``--yes`` is used instead of ``--max-usd`` because gbrain has no price for LiteLLM
        models and refuses any ``--max-usd`` run. The caller limits the chunk count instead.
        """
        return self.run_json("embed", "--stale", "--yes", "--json")

    def embedding_state(self) -> dict:
        """The ``embedding_state`` doctor check: counts of embedded and pending chunks."""
        doc = self.run_json("doctor", "--json", "--only", "embedding_state")
        checks = doc.get("checks") or [{}]
        return {
            "status": checks[0].get("status", "unknown"),
            "message": checks[0].get("message", ""),
            **(checks[0].get("details") or {}),
        }

    def install_pack(self, name: str, yaml_text: str) -> None:
        """Write, validate, lint and activate a schema pack. Lint errors raise GBrainError."""
        pack_dir = self.home / ".gbrain" / "schema-packs" / name
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "pack.yaml").write_text(yaml_text)
        self.run("schema", "validate", name)
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

    def import_dir(self, path: Path) -> dict:
        """Import a directory of markdown pages without embeddings; returns gbrain's summary."""
        return self.run_json("import", str(path), "--no-embed", "--fresh", "--json")

    def extract_links(self) -> dict:
        """Turn frontmatter link fields into typed graph edges for the whole brain."""
        return self.run_json(
            "extract", "links", "--source", "db", "--include-frontmatter", "--json"
        )

    def schema_sync(self) -> None:
        """Backfill page types from the active schema pack."""
        self.run("schema", "sync", "--apply")

    def delete(self, slug: str) -> bool:
        """Soft-delete a page (recoverable for 72 hours). True on success."""
        return self.run("delete", slug, "--force", check=False).returncode == 0

    def schema_stats(self) -> dict:
        """Per-type page counts and typed coverage, as JSON."""
        return self.run_json("schema", "stats", "--json")

    def stats_text(self) -> str:
        """``gbrain stats`` output as text."""
        return self.run("stats").stdout


class Reader:
    """Synchronous facade over one `gbrain serve` MCP stdio session (~0.1 s per call)."""

    def __init__(self, gbrain: GBrain):
        self.gb = gbrain
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._queue: asyncio.Queue | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> Reader:
        self._thread.start()
        self._ready.wait(120)
        if self._error:
            raise GBrainError(f"Could not start gbrain MCP server: {self._error}")
        if not self._ready.is_set():
            raise GBrainError("gbrain MCP server did not start within 120 s")
        return self

    def __exit__(self, *exc) -> None:
        if self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        self._thread.join(30)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker())
        except BaseException as e:  # surfaced to the caller via _error
            self._error = e
            self._ready.set()

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
                while (item := await self._queue.get()) is not None:
                    tool, args, fut = item
                    try:
                        fut.set_result(await session.call_tool(tool, args))
                    except Exception as e:  # returned to the waiting caller
                        fut.set_exception(e)

    def call(self, tool: str, args: dict, timeout: int = 120):
        """Call one MCP tool and return its parsed JSON result (or raw text)."""
        if self._queue is None:
            raise GBrainError("Reader is not started; use it as a context manager")
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (tool, args, fut))
        res = fut.result(timeout)
        text = "".join(getattr(c, "text", "") for c in res.content)
        if getattr(res, "is_error", None) or getattr(res, "isError", None):
            raise GBrainError(f"{tool} {args}: {text[:500]}")
        # The server may append a plain-text notice after the JSON payload.
        try:
            value, _ = json.JSONDecoder().raw_decode(text.lstrip())
            return value
        except json.JSONDecodeError:
            return text

    # ---- typed helpers -----------------------------------------------------------

    def page(self, slug: str) -> dict | None:
        """A page as stored by gbrain, or None if it does not exist."""
        try:
            return self.call("get_page", {"slug": slug})
        except GBrainError as e:
            if "not_found" in str(e) or "not found" in str(e).lower():
                return None
            raise

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

    def query(self, text: str, limit: int = 10) -> list[dict]:
        """Hybrid (keyword + semantic) search."""
        res = self.call("query", {"query": text, "limit": limit}, timeout=300)
        if isinstance(res, dict):
            res = res.get("results", [])
        return res if isinstance(res, list) else []

    def links(self, slug: str) -> list[dict]:
        """Outgoing and incoming links of a page."""
        res = self.call("get_links", {"slug": slug})
        return res if isinstance(res, list) else []

    def search(self, query: str, types: list[str] | None = None, limit: int = 50) -> list[dict]:
        """Keyword search, optionally limited to page types."""
        args = {"query": query, "limit": limit, "match": "keyword"}
        if types:
            args["types"] = types
        res = self.call("search", args)
        if isinstance(res, dict):
            res = res.get("results", [])
        return res if isinstance(res, list) else []
