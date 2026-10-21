"""Ingestion pipeline: convert -> classify -> extract -> write -> import -> link -> verify."""

from __future__ import annotations

import hashlib
import json
import time
import traceback
from collections import deque
from dataclasses import asdict
from pathlib import Path

from . import embedcheck, llm
from .classify import classify
from .confidence import route_sections
from .convert import SUPPORTED, ConversionError, attachment_key, convert
from .extract import Extractor, document_id
from .gbrain import GBrain, GBrainError
from .mdutil import slugify
from .model import DocumentResult, Page
from .state import State
from .verify import render_markdown, verify
from .vocab import Vocabulary
from .writer import write_pages

SHARED = "_shared"


def log(msg: str) -> None:
    """Print a progress line."""
    print(f"[docingest] {msg}", flush=True)


def collect_inputs(path: Path, exclude: list[Path]) -> tuple[list[Path], list[Path]]:
    """Supported files under path (or path itself), sorted; plus skipped files."""
    if path.is_file():
        return ([path], []) if path.suffix.lower() in SUPPORTED else ([], [path])
    files, skipped = [], []
    for p in sorted(path.rglob("*")):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(path).parts):
            continue
        if any(ex in p.parents for ex in exclude):
            continue
        (files if p.suffix.lower() in SUPPORTED else skipped).append(p)
    return files, skipped


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


class Pipeline:
    """Runs ingestion and verification for one brain and work directory."""

    def __init__(
        self,
        work: Path,
        brain_home: Path,
        use_llm: bool = False,
        llm_effort: str = "medium",
        kind: str | None = None,
        llm_mode: str = "auto",
        embedding: tuple[str, int] | None = None,
    ):
        self.work = work
        self.vocab = Vocabulary()
        self.gb = GBrain(brain_home, log_dir=work / "logs")
        self.state = State(work / "state.json")
        self.use_llm = use_llm
        self.llm_effort = llm_effort
        self.llm_mode = llm_mode
        self.kind = kind
        self.embedding = embedding

    # ---- setup ---------------------------------------------------------------

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

    def process(
        self, path: Path, parent: str | None = None, parent_key: str | None = None
    ) -> tuple[DocumentResult, list[Path]]:
        """Convert, classify and extract one file (plus routing and optional AI).

        Returns the result and the attachment files to ingest as separate documents.
        """
        extractor = Extractor(self.vocab)
        attach_tmp = self.work / "attachments" / "_incoming"
        conv, reference = convert(path, attach_tmp)
        kind = classify(conv, self.vocab, self.kind if parent is None else None)
        proposed = (
            attachment_key(parent_key, path)
            if parent_key
            else slugify(document_id(conv.markdown) or path.stem, 50)
        )
        key = self.state.key_for(str(path.resolve()), proposed)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()

        res = extractor.extract(path, conv, reference, kind, key, sha)
        doc = res.pages[0]
        if parent:
            doc.add_link("attachment_of", parent)
        res.routing = route_sections(res, list(extractor._globals.values()))
        low = sum(1 for d in res.routing if d["route"] == "ai")
        log(
            f"  Rule confidence: {len(res.routing) - low} section(s) fully covered by rules, "
            f"{low} low-confidence"
        )
        if self.use_llm:
            report = llm.LLMExtractor(
                self.vocab, extractor, self.llm_effort, mode=self.llm_mode
            ).enrich(res)
            res.globals = list(extractor._globals.values())
            for g in res.globals:
                g.add_link("appears_in", doc.slug)
            log(
                f"  AI extraction ({self.llm_mode}): {report['sections']} section(s) sent, "
                f"{report['skipped_high_confidence']} skipped as high-confidence; "
                f"{report['accepted']} accepted, {report['rejected']} rejected, "
                f"~${report['estimated_cost_usd']} "
                f"({report['input_tokens']} in / {report['output_tokens']} out tokens)"
            )

        attachments = []
        if conv.attachments:
            dest = self.work / "attachments" / key
            dest.mkdir(parents=True, exist_ok=True)
            for a in conv.attachments:
                target = dest / a.name
                a.replace(target)
                # Images, signatures, archives, ... are kept on disk but not ingested.
                if target.suffix.lower() in SUPPORTED:
                    attachments.append(target)
                else:
                    res.warnings.append(f"Attachment {a.name} not ingested: unsupported file type")
        return res, attachments

    def save_document(self, res: DocumentResult) -> list[str]:
        """Write a document's markdown, manifest and pages, and update the state.

        Returns the slugs this document produced before but no longer does.
        """
        for sub in ("markdown", "reference", "manifests"):
            (self.work / sub).mkdir(parents=True, exist_ok=True)
        (self.work / "markdown" / f"{res.key}.md").write_text(res.markdown)
        (self.work / "reference" / f"{res.key}.txt").write_text(res.reference_text)
        section_type = self.vocab.gbrain_type("document_section")
        section_slugs = [p.slug for p in res.pages if p.type == section_type]
        manifest = {
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
            "pages": [page_dict(p) for p in res.pages],
        }
        (self.work / "manifests" / f"{res.key}.json").write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False)
        )
        stale = self.state.record_document(res, res.pages)
        write_pages(res.pages, self.work / "pages" / res.key)
        return stale

    # ---- run -----------------------------------------------------------------

    def ingest(self, inputs: list[Path]) -> dict:
        """Ingest files one after another, then rebuild shared pages, clean up and link.

        Returns the processed document keys, failed files and deleted slugs.
        """
        self.prepare()
        queue = deque((p, None, None) for p in inputs)
        processed, failed, stale = [], [], []
        while queue:
            path, parent_slug, parent_key = queue.popleft()
            label = path.name + (f" (attachment of {parent_key})" if parent_key else "")
            log(f"Processing {label}")
            started = time.time()
            try:
                res, attachments = self.process(path, parent_slug, parent_key)
                stale += self.save_document(res)
                summary = self.gb.import_dir(self.work / "pages" / res.key)
                log(
                    f"  {res.kind}: {len(res.sections)} sections, {len(res.pages)} pages, "
                    f"{len(res.globals)} shared entities; imported {summary.get('imported', 0)} "
                    f"(unchanged {max(summary.get('unchanged', 0), summary.get('skipped', 0))}, "
                    f"errors {summary.get('errors', 0)}), {summary.get('chunks', 0)} chunks "
                    f"in {time.time() - started:.1f}s"
                )
                if summary.get("errors"):
                    raise RuntimeError(f"gbrain import reported errors: {summary.get('failures')}")
                processed.append(res.key)
                for a in attachments:
                    queue.append((a, res.pages[0].slug, res.key))
                self.state.save()
            except ConversionError as e:
                log(f"  FAILED (conversion): {e}")
                failed.append({"file": str(path), "error": str(e)})
            except Exception as e:  # keep going with the remaining documents
                log(f"  FAILED: {e}")
                failed.append(
                    {"file": str(path), "error": str(e), "trace": traceback.format_exc()[-2000:]}
                )

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

        to_delete = sorted(set(stale) | set(orphans))
        for slug in to_delete:
            self.gb.delete(slug)
        if to_delete:
            log(f"Deleted {len(to_delete)} page(s) no longer produced by any document")

        links = self.gb.extract_links()
        log(
            f"Links: {links.get('links_created', 0)} frontmatter links written; "
            f"{links.get('skipped_missing_target', 0)} body-text candidate(s) skipped "
            "(no such page)"
        )
        self.gb.schema_sync()
        self.state.save()
        return {"processed": processed, "failed": failed, "deleted": to_delete}

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
        slugs = sorted(
            {s for d in self.state.documents.values() for s in d.get("pages", [])}
            | set(self.state.data["globals"])
        )
        log(f"Checking embeddings on {len(slugs)} page(s) ...")
        return embedcheck.run(self.gb, self.work, slugs, model, dimensions, queries_file)

    def verify(self, keys: list[str], search_limit: int = 0) -> dict:
        """Verify the given documents and write the reports to ``work/reports``."""
        report = verify(self.work, self.gb, keys, search_limit)
        reports = self.work / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (reports / f"verify-{stamp}.json").write_text(
            json.dumps(report, indent=1, ensure_ascii=False)
        )
        (reports / f"verify-{stamp}.md").write_text(render_markdown(report))
        (reports / "latest.md").write_text(render_markdown(report))
        (reports / "latest.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        return report
