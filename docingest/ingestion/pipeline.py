# What this file does: the main ingest run. For a set of files it runs, in order: convert,
# classify, extract, write, import into gbrain, link, verify. It also applies reviewers' schema
# decisions and undoes any decision that breaks verification.

"""Ingestion pipeline: convert -> classify -> extract -> write -> import -> link -> verify."""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from collections import deque
from dataclasses import asdict
from pathlib import Path

from docingest.utils import clock
from docingest.evaluation import embedding_checks

from docingest.ingestion import extract_with_ai
from docingest.ingestion.detect_document_kind import classify
from docingest.ingestion.rule_confidence import route_sections
from docingest.ingestion.convert_to_markdown import (
    SUPPORTED,
    ConversionError,
    attachment_key,
    convert,
)
from docingest.ingestion.extract_with_rules import DOC_DIR, Extractor, document_id
from docingest.ingestion import review_ai
from docingest.ingestion.review_proposals import ReviewBoard, ReviewError, learned_file
from docingest.utils.gbrain_client import GBrain, GBrainError
from docingest.utils.logger import get_logger, set_document
from docingest.utils.markdown_parsing import slugify
from docingest.utils.data_types import DocumentResult, Page
from docingest.ingestion.ingest_state import State
from docingest.evaluation.verification import render_markdown, verify
from docingest.vocabulary.vocabulary_loader import Vocabulary
from docingest.ingestion.write_pages import write_pages

# Name of the shared-entity folder and manifest. They are rebuilt once per run, after all files.
SHARED = "_shared"

_log = get_logger(__name__)


# Log one progress line at INFO level (the console and the log files).
def log(msg: str) -> None:
    """Log a progress line."""
    _log.info(msg)


# Find the files to ingest: one file, or everything under a folder. Hidden files and folders are
# skipped, and so are excluded folders such as the work folder. Returns (supported, skipped).
def collect_inputs(path: Path, exclude: list[Path]) -> tuple[list[Path], list[Path]]:
    """Supported files under path (or path itself), sorted; plus skipped files."""
    if path.is_file():
        return ([path], []) if path.suffix.lower() in SUPPORTED else ([], [path])
    files, skipped = [], []
    for p in sorted(path.rglob("*")):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(path).parts):
            continue
        # Skip anything inside an excluded folder.
        if any(ex in p.parents for ex in exclude):
            continue
        (files if p.suffix.lower() in SUPPORTED else skipped).append(p)
    return files, skipped


# Turn a page into a plain dict, so the manifest can be saved as JSON.
def page_dict(p: Page) -> dict:
    """A page as a plain dict, for manifests."""
    return {
        "slug": p.slug,
        "type": p.type,
        "title": p.title,
        "fields": p.fields,
        "links": p.links,
        "body": p.body,
    }


# The manifest of one document: what was extracted from it, saved as JSON. The dashboard,
# verification and the gap audit read it.
def document_manifest(res: DocumentResult, section_type: str) -> dict:
    """The manifest of one document, as a plain dict."""
    section_slugs = [p.slug for p in res.pages if p.type == section_type]
    return {
        "key": res.key,
        "source_path": res.source_path,
        "sha256": res.sha256,
        "kind": res.kind,
        "title": res.title,
        "converter": res.converter,
        "warnings": res.warnings,
        "section_type": section_type,
        "sections": [
            {**asdict(s), "slug": slug}
            for s, slug in zip(res.sections, section_slugs, strict=True)
        ],
        "tables": [
            {
                "section": t.section,
                "header": t.header,
                "rows": t.rows,
                "mapped_to": t.mapped_to,
                "id_col": t.id_col,
            }
            for t in res.tables
        ],
        "entity_ids": res.entity_ids,
        "undefined_refs": res.undefined_refs,
        "llm_report": res.llm_report,
        "routing": res.routing,
        "gaps": [{**{k: v for k, v in g.items() if k != "rows"}, "rows": len(g["rows"])} for g in res.gaps],
        "pages": [page_dict(p) for p in res.pages],
    }


# Runs ingestion and verification for one brain and work folder. The shared objects (vocabulary,
# brain connection, state, review board) are created once here and used for every file.
class Pipeline:
    """Runs ingestion and verification for one brain and work directory."""

    # Set up the shared objects for a run. Nothing is written until ingest() or embed() is called.
    def __init__(
        self,
        work: Path,
        brain_home: Path,
        use_llm: bool = False,
        llm_effort: str = "medium",
        kind: str | None = None,
        llm_mode: str = "auto",
        embedding: tuple[str, int] | None = None,
        schema_mode: str = "auto",
    ):
        self.work = work
        self.schema_mode = schema_mode  # auto: rules apply clear gaps | review: reviewers only | off
        # The review board keeps the proposals for gaps the rules could not settle, and the
        # decisions made on them.
        self.board = ReviewBoard(work)
        # The built-in vocabulary plus the reviewed additions.
        self.vocab = Vocabulary(learned=learned_file(work))
        # The connection to the brain (gbrain).
        self.gb = GBrain(brain_home, log_dir=work / "logs")
        # What has been ingested so far. It drives stale-page cleanup and the shared entity pages.
        self.state = State(work / "state.json")
        self.use_llm = use_llm
        self.llm_effort = llm_effort
        self.llm_mode = llm_mode
        self.kind = kind
        self.embedding = embedding

    # ---- setup ---------------------------------------------------------------

    # Run once before any file. Creates the brain if needed, saves the schema pack to
    # doc-pack.yaml, and installs it in gbrain.
    def prepare(self) -> None:
        """Initialize the brain if needed and install the generated schema pack."""
        if self.gb.ensure_initialized(self.embedding):
            what = f"embeddings {self.embedding[0]}" if self.embedding else "embeddings off"
            log(f"Initialized a new brain at {self.gb.home} (PGLite, {what})")
        pack = self.vocab.render_pack()
        (self.work / "doc-pack.yaml").parent.mkdir(parents=True, exist_ok=True)
        (self.work / "doc-pack.yaml").write_text(pack)
        self.gb.install_pack(self.vocab.pack["name"], pack)
        log(f"Schema pack {self.vocab.pack['name']} installed and active")

    # ---- one document ---------------------------------------------------------

    # Step 1 for one file: convert it, decide its kind, extract its pages, and collect its
    # attachments. Nothing is written to the brain yet.
    def process(
        self, path: Path, parent: str | None = None, parent_key: str | None = None
    ) -> tuple[DocumentResult, list[Path]]:
        """Convert, classify and extract one file (plus routing and optional AI).

        Returns the result and the attachment files to ingest as separate documents.
        """
        # A new extractor for each file, so the shared entities of one document do not leak into
        # the next.
        extractor = Extractor(self.vocab)
        # Attachments are written here first, then moved under the document's key.
        attach_tmp = self.work / "attachments" / "_incoming"
        started = time.time()
        conv, reference = convert(path, attach_tmp)
        _log.step("converted", file=path.name, converter=conv.converter,
                  secs=f"{time.time() - started:.1f}", chars=len(conv.markdown), warnings=len(conv.warnings))
        # A --kind given on the command line applies only to the top-level file, not to its
        # attachments.
        kind = classify(conv, self.vocab, self.kind if parent is None else None)
        # The document's key is proposed from its ID or file name. An attachment's key is built
        # from its parent's key.
        proposed = (
            attachment_key(parent_key, path)
            if parent_key
            else slugify(document_id(conv.markdown) or path.stem, 50)
        )
        # A file that was ingested before keeps the same key, so its pages are replaced rather than
        # duplicated.
        key = self.state.key_for(str(path.resolve()), proposed)
        # From here on, every log line carries this document's key.
        set_document(key)
        # A fingerprint of the file's bytes, stored in the manifest.
        sha = hashlib.sha256(path.read_bytes()).hexdigest()

        # The rule-based extraction produces the pages, links and shared entities for the document.
        res = extractor.extract(path, conv, reference, kind, key, sha)
        # The first page is the document itself.
        doc = res.pages[0]
        if parent:
            doc.add_link("attachment_of", parent)
        # Decide for each section whether the rules covered it (see rule_confidence.py).
        res.routing = route_sections(res, list(extractor._globals.values()))
        _log.debug("after rule extraction", sections=len(res.sections), pages=len(res.pages),
                   shared=len(res.globals))
        low = sum(1 for d in res.routing if d["route"] == "ai")
        log(
            f"  Rule confidence: {len(res.routing) - low} section(s) fully covered by rules, "
            f"{low} low-confidence"
        )
        # With --llm, only the low-confidence text is sent to the AI.
        if self.use_llm:
            report = extract_with_ai.LLMExtractor(
                self.vocab, extractor, self.llm_effort, mode=self.llm_mode
            ).enrich(res)
            res.globals = list(extractor._globals.values())
            # Shared entities also link back to this document.
            for g in res.globals:
                g.add_link("appears_in", doc.slug)
            log(
                f"  AI extraction ({self.llm_mode}): {report['sections']} section(s) sent, "
                f"{report['skipped_high_confidence']} skipped as high-confidence; "
                f"{report['accepted']} accepted, {report['rejected']} rejected; "
                f"{report.get('links_accepted', 0)} cross-section link(s) inferred, "
                f"{report.get('links_rejected', 0)} rejected, "
                f"~${report['estimated_cost_usd']} "
                f"({report['input_tokens']} in / {report['output_tokens']} out tokens)"
            )
            # Rejected items are not written anywhere in the brain. Warn, so the loss is visible.
            if report["rejected"]:
                _log.warning(
                    "AI items rejected", rejected=report["rejected"], accepted=report["accepted"],
                    manifest=f"manifests/{key}.json",
                )

        # Attachments are moved into the work folder under the document's key, so they stay with
        # it.
        attachments = []
        if conv.attachments:
            dest = self.work / "attachments" / key
            dest.mkdir(parents=True, exist_ok=True)
            for a in conv.attachments:
                target = dest / a.name
                a.replace(target)
                # Images, signatures, archives, ... are kept on disk but not ingested. Only
                # supported types are ingested as documents. Other attachments (images, signatures)
                # are kept on disk and reported as not ingested.
                if target.suffix.lower() in SUPPORTED:
                    attachments.append(target)
                else:
                    res.warnings.append(f"Attachment {a.name} not ingested: unsupported file type")
        return res, attachments

    # Step 2 for one document: write its markdown, reference text and manifest, record it in the
    # state, and write its pages as files. Returns the pages it produced last time but not now.
    def save_document(self, res: DocumentResult) -> list[str]:
        """Write a document's markdown, manifest and pages, and update the state.

        Returns the slugs this document produced before but no longer does.
        """
        for sub in ("markdown", "reference", "manifests"):
            (self.work / sub).mkdir(parents=True, exist_ok=True)
        (self.work / "markdown" / f"{res.key}.md").write_text(res.markdown)
        (self.work / "reference" / f"{res.key}.txt").write_text(res.reference_text)
        section_type = self.vocab.gbrain_type("document_section")
        manifest = document_manifest(res, section_type)
        (self.work / "manifests" / f"{res.key}.json").write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False)
        )
        # Pages this document produced last time but does not produce now. They are deleted later.
        stale = self.state.record_document(res, res.pages)
        # The pages are written as files, ready for gbrain's import step.
        write_pages(res.pages, self.work / "pages" / res.key)
        return stale

    # ---- run -----------------------------------------------------------------

    # ---- schema gaps and review -----------------------------------------------

    # Send the document's gaps to the review board. Pages that depend on an unsettled gap are
    # marked, so they are easy to find later.
    def register_gaps(self, res: DocumentResult) -> None:
        """Record the document's gaps as proposals and mark the pages they affect."""
        if self.schema_mode == "off" or not res.gaps:
            return
        self.board.add_gaps(res.key, res.gaps, self.vocab, self.schema_mode)
        by_slug = {p.slug: p for p in res.pages}
        for gap in res.gaps:
            for row in gap["rows"]:
                page = by_slug.get(row["slug"])
                if page is None:
                    continue
                page.fields["schema_status"] = "unmapped" if gap.get("status") == "unmapped" else "provisional"
                if gap.get("proposal"):
                    page.fields["proposal"] = gap["proposal"]

    # Rebuild the vocabulary from the learned file and reinstall the schema pack, after a decision
    # has changed it.
    def _reload_vocabulary(self) -> None:
        """Use the learned vocabulary as it is now, and reinstall the schema pack."""
        self.vocab = Vocabulary(learned=learned_file(self.work))
        self.prepare()

    # Read the given documents again from their source files, using the vocabulary as it is now.
    def _reingest(self, keys: list[str], state: dict) -> None:
        """Process the given documents again (a decision changed how they are read)."""
        queue = deque()
        for key in sorted(set(keys)):
            doc = self.state.documents.get(key)
            if not doc or not Path(doc["source_path"]).exists():
                log(f"  Cannot re-read {key}: source file is gone")
                continue
            parent = doc.get("parent")
            # An attachment's parent is passed as the parent's slug, so the attachment links back
            # to it.
            queue.append((Path(doc["source_path"]), f"{DOC_DIR}/{parent}" if parent else None, parent))
        self._run(queue, state)

    # Step 0 of a run: apply the decisions reviewers approved, and re-read the documents they
    # affect.
    def apply_reviews(self, state: dict) -> list[dict]:
        """Apply decisions reviewers recorded; re-read the documents they affect.

        Returns what was applied: proposal ID, documents, the learned file before it.
        """
        # Make sure the review files on disk match the proposals before they are read.
        self.board.sync_files()
        applied = []
        for pid, p in self.board.pending().items():
            # Check whether the decision can be applied now. When it cannot, 'why' says what is
            # missing.
            action, why = self.board.ready(p, self.vocab)
            if action is None:
                if p["decision"] != "pending":
                    log(f"Review {pid} not applied yet: {why}")
                continue
            # Keep the learned file as it was, so the decision can be rolled back.
            before = learned_file(self.work).read_text() if learned_file(self.work).exists() else None
            # Everyone whose approval matches the decision. Their names are recorded with it.
            who = sorted({a["by"] for a in p["approvals"] if a["decision"] == p["decision"]})
            self.board.commit(p, action, ", ".join(who), "reviewer")
            log(f"Review {pid} applied: {p['decision']} by {', '.join(who)}")
            applied.append({"id": pid, "documents": list(p["documents"]), "before": before})
        self._take_effect(applied, state)
        return applied

    # Apply the decisions: reload the vocabulary and re-read the affected documents. If that fails,
    # undo the decisions and raise the error.
    def _take_effect(self, applied: list[dict], state: dict) -> None:
        """Reinstall the schema pack and read the affected documents again.

        If that fails, the decisions are undone before the error goes on, so the learned
        vocabulary never claims something the brain does not have.
        """
        if not applied:
            return
        try:
            self._reload_vocabulary()
            self._reingest([k for a in applied for k in a["documents"]], state)
        except Exception:
            for a in reversed(applied):
                self.board.rollback(a["id"], "could not be applied: the documents could not be read again")
            self._reload_vocabulary()
            raise

    # Step 3 of a run: the rules apply proposals whose checks all pass (in auto mode), and with
    # --llm the AI suggests an action for the rest. The AI never applies its own suggestions.
    def auto_review(self, state: dict) -> list[dict]:
        """Rules apply the proposals whose checks all pass; the AI suggests for the rest."""
        applied = []
        if self.schema_mode == "auto":
            for pid, p in self.board.pending().items():
                # Only proposals the rules marked as safe to apply on their own.
                if not p.get("auto"):
                    continue
                # The rules' own suggested action. It is checked again when it is committed.
                action = dict(p["rules_suggestion"])
                try:
                    before = learned_file(self.work).read_text() if learned_file(self.work).exists() else None
                    self.board.commit(p, action, "rules", "auto")
                # A check failed when the decision was committed. Record why, and leave the
                # proposal for a reviewer.
                except ReviewError as e:
                    p["refused"] = str(e)
                    self.board.save(p)
                    continue
                log(f"Review {pid} applied by the rules: {p['found']} -> {action['action']} {action.get('name', '')}")
                applied.append({"id": pid, "documents": list(p["documents"]), "before": before})
            self._take_effect(applied, state)
        # Ask the AI for suggestions on open proposals that have none yet.
        if self.use_llm and self.schema_mode != "off":
            # Open proposals that have no AI suggestion yet and are not handled automatically.
            todo = {pid: p for pid, p in self.board.pending().items() if not p.get("ai_suggestion") and not p.get("auto")}
            if todo:
                n = review_ai.suggest_all(self.board, todo, self.vocab, effort=self.llm_effort)
                log(f"AI suggested for {n} of {len(todo)} open proposal(s)")
        self.board.write_pending_md()
        return applied

    # Step 5 of a run: verify the documents the decisions changed. If verification fails, undo the
    # decisions, re-read the documents, and return the IDs that were undone.
    def guard(self, applied: list[dict], state: dict) -> list[str]:
        """Verify the documents a decision changed; undo the decisions if verification fails.

        Returns the IDs rolled back.
        """
        # Only documents processed in this run are checked.
        keys = sorted({k for a in applied for k in a["documents"] if k in state["processed"]})
        if not applied or not keys:
            return []
        # A search limit of 1 keeps this check quick: it tests one section for keyword search.
        report = self.verify(keys, 1)
        if report["summary"]["status"] != "fail":
            return []
        # Undo the most recent decision first.
        bad = [a["id"] for a in reversed(applied)]
        for pid in bad:
            self.board.rollback(pid, "verification failed after applying")
            log(f"Review {pid} rolled back: verification failed after applying it")
        self._reload_vocabulary()
        self._reingest(keys, state)
        return bad

    # The work loop: each queued file is processed, saved and imported. A file that fails is
    # recorded and the loop carries on with the rest.
    def _run(self, queue: deque, state: dict) -> None:
        """Process the queue: convert, extract, write, import. Attachments join the queue."""
        while queue:
            path, parent_slug, parent_key = queue.popleft()
            label = path.name + (f" (attachment of {parent_key})" if parent_key else "")
            # Until the document key is known, lines carry the file name instead.
            set_document(path.stem)
            _log.info("processing", file=label)
            started = time.time()
            try:
                res, attachments = self.process(path, parent_slug, parent_key)
                # Record the schema gaps for review before the pages are saved.
                self.register_gaps(res)
                # Collect the pages this document no longer produces. They are deleted in _finish.
                state["stale"] += self.save_document(res)
                if parent_key:
                    self.state.documents[res.key]["parent"] = parent_key
                # Import the written pages into gbrain. The summary says how many were imported and
                # how many failed.
                _log.info("importing pages", pages=len(res.pages))
                summary = self.gb.import_dir(self.work / "pages" / res.key)
                _log.step(
                    "document imported",
                    kind=res.kind,
                    sections=len(res.sections),
                    pages=len(res.pages),
                    shared=len(res.globals),
                    imported=summary.get("imported", 0),
                    unchanged=max(summary.get("unchanged", 0), summary.get("skipped", 0)),
                    errors=summary.get("errors", 0),
                    chunks=summary.get("chunks", 0),
                    secs=f"{time.time() - started:.1f}",
                )
                if res.gaps:
                    _log.warning("schema gaps", gaps=", ".join(
                        g["key"] + " -> " + g.get("proposal", g.get("status", "report only")) for g in res.gaps))
                # Import errors fail this document, so it is not marked as processed.
                if summary.get("errors"):
                    raise RuntimeError(f"gbrain import reported errors: {summary.get('failures')}")
                if res.key not in state["processed"]:
                    state["processed"].append(res.key)
                state["timings"][res.key] = round(time.time() - started, 2)
                if parent_key:
                    state["parents"][res.key] = parent_key
                # Attachments join the queue, so they are ingested in this same run, after their
                # parent.
                for a in attachments:
                    queue.append((a, res.pages[0].slug, res.key))
                self.state.save()
            # A file that cannot be converted is recorded as failed. No traceback is kept, because
            # the reason is clear.
            except ConversionError as e:
                _log.error("conversion failed", reason=e)
                state["failed"].append({"file": str(path), "error": str(e), "parent": parent_key})
            except Exception as e:  # keep going with the remaining documents
                _log.error("document failed", reason=e)
                state["failed"].append(
                    {
                        "file": str(path),
                        "error": str(e),
                        "parent": parent_key,
                        "trace": traceback.format_exc()[-2000:],
                    }
                )

    # Step 4 after all files: write the shared entity pages, delete stale pages, turn the
    # frontmatter links into graph edges, and set each page's type. Returns the deleted slugs.
    def _finish(self, state: dict) -> list[str]:
        """Rebuild shared pages, delete pages nothing produces, link, sync. Returns deleted slugs."""
        # Rebuild each shared entity from every document that mentions it. Orphans are entities
        # nobody mentions now.
        shared_pages, orphans = self.state.global_pages(self.vocab)
        write_pages(shared_pages, self.work / "pages" / SHARED)
        (self.work / "manifests").mkdir(parents=True, exist_ok=True)
        (self.work / "manifests" / f"{SHARED}.json").write_text(
            json.dumps(
                {"pages": [page_dict(p) for p in shared_pages]}, indent=1, ensure_ascii=False
            )
        )
        if shared_pages:
            summary = self.gb.import_dir(self.work / "pages" / SHARED)
            log(
                f"Shared entities: {len(shared_pages)} pages "
                f"(imported {summary.get('imported', 0)})"
            )

        # Pages no document produces now, plus shared entities that no document mentions any more.
        to_delete = sorted(set(state["stale"]) | set(orphans))
        for slug in to_delete:
            self.gb.delete(slug)
        if to_delete:
            log(f"Deleted {len(to_delete)} page(s) no longer produced by any document")
        state["stale"] = []

        # Turn the frontmatter link fields into graph edges.
        links = self.gb.extract_links()
        log(
            f"Links: {links.get('links_created', 0)} frontmatter links written; "
            f"{links.get('skipped_missing_target', 0)} body-text candidate(s) skipped "
            "(no such page)"
        )
        # Give each page its type from the schema pack.
        self.gb.schema_sync()
        self.state.save()
        return to_delete

    # Run a whole ingest and return its summary. Order: apply the approved reviews, process the
    # inputs, apply the rules' and AI's suggestions, clean up, then undo any decision that failed
    # verification.
    def ingest(self, inputs: list[Path]) -> dict:
        """Ingest files one after another, then rebuild shared pages, clean up and link.

        Order: apply reviewers' decisions (re-reading the documents they change), ingest the
        inputs, let the rules apply clear schema gaps, finish, and undo a decision that made
        verification fail. Returns the processed document keys, failed files, deleted slugs
        and the open and applied proposals.
        """
        # Set up the brain and the schema pack before any file is read.
        self.prepare()
        state = {"processed": [], "failed": [], "stale": [], "timings": {}, "parents": {}}
        deleted: list[str] = []
        # Hold the review lock for the whole run, so two runs cannot change the same proposals at
        # once.
        with self.board.lock():
            applied = self.apply_reviews(state)
            self._run(deque((p, None, None) for p in inputs), state)
            applied += self.auto_review(state)
            deleted += self._finish(state)
            # Check what the decisions changed, and undo any that broke verification.
            rolled_back = self.guard(applied, state)
            if rolled_back:
                deleted += self._finish(state)
            self.board.write_pending_md()
        return {
            "processed": state["processed"],
            "failed": state["failed"],
            "deleted": deleted,
            "timings": state["timings"],
            "parents": state["parents"],
            "proposals_pending": sorted(self.board.pending()),
            "proposals_applied": [a["id"] for a in applied if a["id"] not in rolled_back],
            "proposals_rolled_back": rolled_back,
        }

    # Create vectors for the pending chunks, then run the embedding checks. Refused when embeddings
    # are off, or when more chunks are pending than the limit.
    def embed(
        self,
        model: str,
        dimensions: int,
        max_chunks: int = 20000,
        queries_file: Path | None = None,
    ) -> dict:
        """Embed pending chunks, then run the embedding checks over every ingested page.

        Raises GBrainError if embeddings are off or more than ``max_chunks`` are pending.
        """
        if not self.gb.embeddings_enabled():
            raise GBrainError(
                "Embeddings are not enabled on this brain. Run: ./ingest.sh embeddings-setup"
            )
        pending = self.gb.embedding_state().get("counts", {}).get("pending", 0)
        # Stop if there are more pending chunks than the limit, so a large and costly run is never
        # started by accident.
        if pending > max_chunks:
            raise GBrainError(
                f"{pending} chunks are pending, more than --max-chunks {max_chunks}. "
                "Raise --max-chunks if that is expected."
            )
        if pending:
            log(f"Embedding {pending} chunk(s) with {model} ...")
            started = time.time()
            res = self.gb.embed_stale()
            log(
                f"  embedded {res.get('embedded', 0)}, failures {res.get('failures', 0)} "
                f"in {time.time() - started:.1f}s"
            )
        else:
            log("No pending chunks to embed")
        # Every page of every ingested document, plus the shared entity pages, is checked.
        slugs = sorted(
            {s for d in self.state.documents.values() for s in d.get("pages", [])}
            | set(self.state.data["globals"])
        )
        log(f"Checking embeddings on {len(slugs)} page(s) ...")
        return embedding_checks.run(self.gb, self.work, slugs, model, dimensions, queries_file)

    # Verify the given documents and save the report: a timestamped JSON and markdown file, and
    # latest.json and latest.md for quick reading.
    def verify(self, keys: list[str], search_limit: int = 0) -> dict:
        """Verify the given documents and write the reports to ``work/reports``."""
        _log.info("verifying documents", documents=len(keys))
        started = time.time()
        report = verify(self.work, self.gb, keys, search_limit)
        _log.step("verification finished", secs=f"{time.time() - started:.1f}", status=report["summary"]["status"])
        reports = self.work / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        # The timestamp keeps each run's report. latest.* is overwritten every time.
        stamp = clock.compact()
        (reports / f"verify-{stamp}.json").write_text(
            json.dumps(report, indent=1, ensure_ascii=False)
        )
        (reports / f"verify-{stamp}.md").write_text(render_markdown(report))
        (reports / "latest.md").write_text(render_markdown(report))
        (reports / "latest.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        return report
