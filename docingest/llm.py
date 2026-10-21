"""Optional AI extraction with Claude (enabled with --llm).

Runs after rule-based extraction. In the default "auto" mode only sections the
confidence router marks low-confidence are sent, and only their residual text
(what the rules did not consume); "all" sends every section in full. Output is
constrained to the vocabulary's types and verbs through structured output. Every entity and
relationship must quote its evidence verbatim from the section; anything
whose quote is not found, whose endpoints don't resolve, or whose verb
points at the wrong target type is rejected and listed in the report.
"""

from __future__ import annotations

import json
import os

from .confidence import ai_input
from .extract import Extractor
from .mdutil import normalize_ws, slugify
from .model import DocumentResult, Page
from .vocab import Vocabulary

MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00, 20.00  # USD per million tokens (claude-opus-5-5)
STRUCTURAL_TYPES = {"source_document", "document_section", "record"}
STRUCTURAL_VERBS = {"defined_in", "section_of", "mentions_item", "appears_in", "attachment_of"}
MIN_SECTION_CHARS = 80


def credentials_available() -> bool:
    """True if an Anthropic API key or auth token is set in the environment."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


INTRO = (
    "You extract a knowledge graph from one section of a business document (BRD, PRD, SOW, "
    "contract, email, meeting transcript, chat, or similar).\n\n"
    "Extract only what the section states explicitly. Do not infer, summarize across sections, "
    "or invent identifiers."
)
RULES = "\n".join(
    [
        '- Lines like "[Table already extracted as ...]" stand for tables whose rows are already '
        "in Known items; do not re-extract their rows, but do link prose to them. Lines after "
        'such a placeholder, like "FR-001 Description: ...", are cells of those rows: extract '
        "the relationships they state, with the row's ID as the endpoint and the cell text as "
        "evidence.",
        '- Items listed under "Known items" already exist. Refer to them by their key; never '
        "re-extract them as new entities.",
        "- Give each new entity a short key unique within your answer (E1, E2, ...).",
        "- `evidence` must be an exact, contiguous quote copied from the section text (under 300 "
        "characters). Items whose quote does not appear verbatim are discarded.",
        "- Attributes are short facts stated about the entity (priority, due date, status, "
        "amount, owner role, ...).",
        "- Use `references` only when no specific verb fits.",
        "- Return empty lists when the section contains nothing to extract.",
    ]
)


def _system_prompt(vocab: Vocabulary) -> str:
    types = "\n".join(
        f"- {t.name}: {t.description}"
        for t in vocab.types.values()
        if t.name not in STRUCTURAL_TYPES
    )
    verbs = "\n".join(
        f"- {v.name}"
        + (f" (target must be {v.target})" if v.target else "")
        + f": {v.description}"
        for v in vocab.verbs.values()
        if v.name not in STRUCTURAL_VERBS
    )
    return f"""{INTRO}

Entity types:
{types}

Relationship verbs (from -> to):
{verbs}

Rules:
{RULES}"""


def _schema(vocab: Vocabulary) -> dict:
    types = [t for t in vocab.types if t not in STRUCTURAL_TYPES]
    verbs = [v for v in vocab.verbs if v not in STRUCTURAL_VERBS]
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "type": {"type": "string", "enum": types},
                        "name": {"type": "string"},
                        "attributes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["name", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "evidence": {"type": "string"},
                    },
                    "required": ["key", "type", "name", "attributes", "evidence"],
                    "additionalProperties": False,
                },
            },
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "verb": {"type": "string", "enum": verbs},
                        "to": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["from", "verb", "to", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["entities", "relationships"],
        "additionalProperties": False,
    }


class LLMExtractor:
    """Claude-based extraction for sections the rules could not fully read."""

    def __init__(
        self,
        vocab: Vocabulary,
        extractor: Extractor,
        effort: str = "medium",
        client=None,
        mode: str = "auto",
    ):
        import anthropic

        self.v, self.x, self.effort, self.mode = vocab, extractor, effort, mode
        self.client = client or anthropic.Anthropic(max_retries=4)
        self.system = _system_prompt(vocab)
        self.schema = _schema(vocab)

    def _known(self, res: DocumentResult) -> dict[str, tuple[str, str, str]]:
        """key -> (slug, vocab_type, name) for items the model may refer to."""
        known: dict[str, tuple[str, str, str]] = {}
        gtypes = {self.v.gbrain_type(t): t for t in self.v.types}
        for p in res.pages:
            vt = gtypes.get(p.type, p.type)
            if vt in STRUCTURAL_TYPES:
                continue
            known[p.fields.get("item_id") or p.title] = (p.slug, vt, p.title)
        for g in self.x._globals.values():
            known[f"{g.vocab_type}:{g.name}"] = (g.slug, g.vocab_type, g.name)
        return known

    def _call(self, user: list[dict]) -> tuple[dict | None, str | None, dict]:
        import anthropic

        try:
            resp = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[
                    {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}
                ],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": self.schema},
                },
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.RateLimitError as e:
            return None, f"rate limited after retries: {e.message}", {}
        except anthropic.APIStatusError as e:
            return None, f"API error {e.status_code}: {e.message}", {}
        except anthropic.APIConnectionError as e:
            return None, f"connection error: {e}", {}
        usage = {
            "input": resp.usage.input_tokens or 0,
            "output": resp.usage.output_tokens or 0,
            "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        }
        if resp.stop_reason == "refusal":
            return None, "model declined the section", usage
        if resp.stop_reason == "max_tokens":
            return None, "output hit max_tokens; section skipped", usage
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text), None, usage
        except json.JSONDecodeError as e:
            return None, f"invalid JSON: {e}", usage

    def enrich(self, res: DocumentResult) -> dict:
        """Extract from the document's sections with Claude and merge accepted items into ``res``.

        Returns the report: sections sent, accepted and rejected items, tokens
        and estimated cost. The report is also stored on ``res.llm_report``.
        """
        report = {
            "model": MODEL,
            "effort": self.effort,
            "mode": self.mode,
            "sections": 0,
            "sent": [],
            "skipped_high_confidence": 0,
            "accepted": 0,
            "rejected": 0,
            "rejections": [],
            "errors": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
        }
        doc_slug = self.x.doc_slug(res.key)
        section_pages = {
            p.fields.get("section_index"): p
            for p in res.pages
            if p.type == self.v.gbrain_type("document_section")
        }
        pages_by_slug = {p.slug: p for p in res.pages}

        routes = {d["section"]: d for d in res.routing}
        # Items from rule extraction: identical for every section, so cached after the first call.
        base_known = self._known(res)
        doc_block = {
            "type": "text",
            "cache_control": {"type": "ephemeral"},
            "text": f"Document: {res.title} (kind: {res.kind})\n\nKnown items:\n"
            + (
                "\n".join(f'- {k}: {vt} "{n}"' for k, (_, vt, n) in base_known.items()) or "(none)"
            ),
        }
        for s in res.sections:
            full = s.text(res.markdown)
            if self.mode == "auto":
                if routes.get(s.index + 1, {}).get("route") != "ai":
                    report["skipped_high_confidence"] += 1
                    continue
                text = ai_input(res, routes[s.index + 1])
            else:
                if len(full.strip()) < MIN_SECTION_CHARS:
                    continue
                text = full
            known = self._known(res)
            added = {k: v for k, v in known.items() if k not in base_known}
            extra = (
                (
                    "Also known (added from earlier sections):\n"
                    + "\n".join(f'- {k}: {vt} "{n}"' for k, (_, vt, n) in added.items())
                    + "\n\n"
                )
                if added
                else ""
            )
            user = [
                doc_block,
                {
                    "type": "text",
                    "text": f"{extra}Section {s.index + 1}: {s.heading}\n\n"
                    f"<section>\n{text}\n</section>",
                },
            ]
            data, error, usage = self._call(user)
            report["sections"] += 1
            report["sent"].append(s.index + 1)
            report["input_tokens"] += usage.get("input", 0)
            report["output_tokens"] += usage.get("output", 0)
            report["cache_read_tokens"] += usage.get("cache_read", 0)
            if error:
                report["errors"].append(f"section {s.index + 1}: {error}")
                continue
            self._apply(
                res,
                s,
                full,
                data,
                known,
                doc_slug,
                section_pages.get(s.index + 1),
                pages_by_slug,
                report,
            )

        report["estimated_cost_usd"] = round(
            report["input_tokens"] * PRICE_IN / 1e6 + report["output_tokens"] * PRICE_OUT / 1e6, 4
        )
        res.llm_report = report
        return report

    def _apply(
        self, res, s, text, data, known, doc_slug, section_page, pages_by_slug, report
    ) -> None:
        norm_text = normalize_ws(text).lower()

        def reject(reason: str) -> None:
            report["rejected"] += 1
            report["rejections"].append(f"section {s.index + 1}: {reason}")

        def evidenced(quote: str) -> bool:
            q = normalize_ws(quote).lower()
            return bool(q) and q in norm_text

        local: dict[str, tuple[str, str]] = {}  # model key -> (slug, vocab_type)
        names = {(vt, normalize_ws(n).lower()): slug for slug, vt, n in known.values()}
        for e in data.get("entities", []):
            if not evidenced(e["evidence"]):
                reject(f"entity '{e['name']}' ({e['type']}): evidence not found verbatim")
                continue
            vt, name = e["type"], e["name"].strip()
            existing = names.get((vt, normalize_ws(name).lower()))
            attrs = {
                self.x.safe_key(a["name"], vt): a["value"]
                for a in e["attributes"]
                if a["value"].strip()
            }
            if self.v.types[vt].scope == "global":
                g = self.x._global(vt, name)
                g.attributes.update({k: v for k, v in attrs.items() if k not in g.attributes})
                g.notes.append(
                    f'Mentioned in section {s.index + 1} ({s.heading}): "{e["evidence"].strip()}"'
                )
                local[e["key"]] = (g.slug, vt)
            elif existing:
                local[e["key"]] = (existing, vt)
            else:
                slug = self.x.entity_slug(vt, res.key, slugify(name, 40))
                if slug in pages_by_slug:
                    local[e["key"]] = (slug, vt)
                    continue
                page = Page(
                    slug=slug,
                    type=self.v.gbrain_type(vt),
                    title=name,
                    fields={
                        "doc_key": res.key,
                        "doc_kind": res.kind,
                        "extracted_by": "ai",
                        "section": f"{s.index + 1}. {s.heading}",
                        **attrs,
                    },
                )
                page.add_link("defined_in", doc_slug)
                page.body = (
                    "\n".join(
                        [
                            f"# {name}",
                            "",
                            f"From {res.title} ({res.key}), section {s.index + 1}: {s.heading}.",
                            "",
                            f"> {e['evidence'].strip()}",
                            "",
                            *[f"- **{a['name']}:** {a['value']}" for a in e["attributes"]],
                        ]
                    )
                    + "\n"
                )
                res.pages.append(page)
                pages_by_slug[slug] = page
                local[e["key"]] = (slug, vt)
            report["accepted"] += 1
            if section_page is not None:
                section_page.add_link("mentions_item", local[e["key"]][0])

        def resolve(key: str) -> tuple[str, str] | None:
            if key in local:
                return local[key]
            if key in known:
                return known[key][0], known[key][1]
            return None

        for r in data.get("relationships", []):
            src, dst, verb = resolve(r["from"]), resolve(r["to"]), r["verb"]
            if not evidenced(r["evidence"]):
                reject(f"{r['from']} --{verb}--> {r['to']}: evidence not found verbatim")
                continue
            if not src or not dst:
                reject(f"{r['from']} --{verb}--> {r['to']}: unknown endpoint")
                continue
            target = self.v.verbs[verb].target
            if target and dst[1] != target:
                reject(
                    f"{r['from']} --{verb}--> {r['to']}: "
                    f"{verb} must point at {target}, not {dst[1]}"
                )
                continue
            if src[0] in pages_by_slug:
                pages_by_slug[src[0]].add_link(verb, dst[0])
            elif src[0] in self.x._globals:
                self.x._globals[src[0]].add_link(verb, dst[0])
            else:
                reject(f"{r['from']} --{verb}--> {r['to']}: source is not a page of this document")
                continue
            report["accepted"] += 1
