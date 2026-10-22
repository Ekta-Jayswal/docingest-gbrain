# What this file does: the optional AI step (`--llm`). It sends sections that the rules could
# not read to the LLM, and only accepts an item if its quote appears word for word in the
# section and its links point at real items of the right type.

"""Optional AI extraction through the LiteLLM proxy (enabled with --llm).

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

from docingest.ingestion.rule_confidence import ai_input
from docingest.ingestion.extract_with_rules import Extractor
from docingest.utils.markdown_parsing import normalize_ws, slugify
from docingest.utils.data_types import DocumentResult, Page
from docingest.utils.llm_client import ChatClient, LLMError, model_name, prices, settings
from docingest.vocabulary.vocabulary_loader import Vocabulary

# The chat model used for extraction. It comes from LLM_MODEL in .env.local.
MODEL = model_name()
PRICE_IN, PRICE_OUT = prices()  # USD per million tokens, from LLM_PRICE_IN / LLM_PRICE_OUT
# Page types that the rules create to hold the document's structure. The AI never extracts these.
STRUCTURAL_TYPES = {"source_document", "document_section", "record"}
# Links the rules write for structure. The AI is not offered these verbs.
STRUCTURAL_VERBS = {"defined_in", "section_of", "mentions", "appears_in", "attachment_of"}
# In `all` mode, sections shorter than this are not sent, since they hold nothing worth extracting.
MIN_SECTION_CHARS = 80


# True when the proxy address and key are set, so the --llm step can run. The command checks this
# first.
def credentials_available() -> bool:
    """True if LITELLM_BASE_URL and LITELLM_API_KEY are both set in the environment."""
    base, key = settings()
    return bool(base and key)


# Opening of the system prompt: what the AI is doing, and that it must extract only what
# the section states.
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


# Build the system prompt: the intro, the entity types the AI may use, the verbs it may use,
# then the rules.
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


# JSON schema for the AI's answer. The allowed types and verbs are listed as enums, so the
# answer cannot name one that does not exist.
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


# Runs the AI over sections and merges the accepted items into the document.
class LLMExtractor:
    """LLM-based extraction for sections the rules could not fully read."""

    # Set up the extractor. A client can be passed in (tests do this); otherwise a real one is
    # made.
    def __init__(
        self,
        vocab: Vocabulary,
        extractor: Extractor,
        effort: str = "medium",
        client=None,
        mode: str = "auto",
    ):
        self.v, self.x, self.effort, self.mode = vocab, extractor, effort, mode
        # Tests pass a fake client; otherwise a client for the LiteLLM proxy is made from the
        # environment.
        self.client = client or ChatClient(*settings())
        self.system = _system_prompt(vocab)
        self.schema = _schema(vocab)

    # Build the items the AI may refer to: the items from rule extraction, and the shared entities.
    # Each maps to (slug, vocabulary type, name).
    def _known(self, res: DocumentResult) -> dict[str, tuple[str, str, str]]:
        """key -> (slug, vocab_type, name) for items the model may refer to."""
        known: dict[str, tuple[str, str, str]] = {}
        # Map stored page types back to vocabulary types.
        gtypes = {self.v.gbrain_type(t): t for t in self.v.types}
        for p in res.pages:
            vt = gtypes.get(p.type, p.type)
            if vt in STRUCTURAL_TYPES:
                continue
            # An item is known by its ID when it has one, otherwise by its title.
            known[p.fields.get("item_id") or p.title] = (p.slug, vt, p.title)
        for g in self.x._globals.values():
            known[f"{g.vocab_type}:{g.name}"] = (g.slug, g.vocab_type, g.name)
        return known

    # Make one request to the chat model for one section. Returns (parsed answer, error message,
    # token usage). Errors are returned rather than raised, so one bad section does not stop the
    # run.
    def _call(self, user: list[dict]) -> tuple[dict | None, str | None, dict]:
        # The section prompt is sent as one text message; the system prompt carries the rules.
        user_text = "\n\n".join(block["text"] for block in user)
        try:
            text, usage = self.client.complete(self.system, user_text, self.schema, "extraction")
        # Failures become error messages for the report. The section is skipped and the run goes
        # on.
        except LLMError as e:
            return None, str(e), {}
        # The answer is the JSON text of the reply.
        try:
            return json.loads(text), None, usage
        except json.JSONDecodeError as e:
            return None, f"invalid JSON: {e}", usage

    # Extract from the document's sections with the LLM and merge the accepted items into the
    # document. Returns the report: sections sent, items accepted and rejected, tokens and
    # estimated cost.
    def enrich(self, res: DocumentResult) -> dict:
        """Extract from the document's sections with the LLM and merge accepted items into ``res``.

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
        # Section pages by section number, so the items found in a section can be linked from it.
        section_pages = {
            p.fields.get("section_index"): p
            for p in res.pages
            if p.type == self.v.gbrain_type("document_section")
        }
        pages_by_slug = {p.slug: p for p in res.pages}

        # The routing decision for each section, from the rule-confidence step.
        routes = {d["section"]: d for d in res.routing}
        # Items from rule extraction: identical for every section, so cached after the first call.
        base_known = self._known(res)
        doc_block = {
            "type": "text",
            "text": f"Document: {res.title} (kind: {res.kind})\n\nKnown items:\n"
            + (
                "\n".join(f'- {k}: {vt} "{n}"' for k, (_, vt, n) in base_known.items()) or "(none)"
            ),
        }
        # Walk the sections. In auto mode only the routed ones are sent. In all mode, every section
        # long enough is sent in full.
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
            # Items found in earlier sections of this run. They are listed for the AI, so it links
            # to them rather than creating them again.
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
            # Ask the model about this section, and count its tokens for the cost estimate.
            data, error, usage = self._call(user)
            report["sections"] += 1
            report["sent"].append(s.index + 1)
            report["input_tokens"] += usage.get("input", 0)
            report["output_tokens"] += usage.get("output", 0)
            report["cache_read_tokens"] += usage.get("cache_read", 0)
            # A failed section is recorded and skipped. The other sections are still processed.
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

        # Estimated cost in USD, from the token counts and the model's prices.
        report["estimated_cost_usd"] = round(
            report["input_tokens"] * PRICE_IN / 1e6 + report["output_tokens"] * PRICE_OUT / 1e6, 4
        )
        res.llm_report = report
        return report

    # Check each item the AI returned and add the accepted ones to the document. Every rejection
    # is recorded with its reason.
    def _apply(
        self, res, s, text, data, known, doc_slug, section_page, pages_by_slug, report
    ) -> None:
        # The section text, normalised, so a quote can be found in it whatever its spacing or case.
        norm_text = normalize_ws(text).lower()

        # Count a rejected item and record the reason in the report.
        def reject(reason: str) -> None:
            report["rejected"] += 1
            report["rejections"].append(f"section {s.index + 1}: {reason}")

        # An item counts only when its quote appears in the section text.
        def evidenced(quote: str) -> bool:
            q = normalize_ws(quote).lower()
            return bool(q) and q in norm_text

        local: dict[str, tuple[str, str]] = {}  # model key -> (slug, vocab_type)
        # Existing items by (type, name), so an entity named twice is not created twice.
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
            # Shared entities (such as people or companies) are not written as pages of this
            # document. They are merged across all documents later.
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
                # Build the page slug for the new entity from its type, its document and its name.
                slug = self.x.entity_slug(vt, res.key, slugify(name, 40))
                # Already created earlier in this run, so reuse it.
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
                # Every new entity links back to the document it came from.
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
            # Each accepted item is also linked from the section it came from.
            if section_page is not None:
                section_page.add_link("mentions", local[e["key"]][0])

        # Find the item a key refers to: a new entity from this answer, or an item already known.
        def resolve(key: str) -> tuple[str, str] | None:
            if key in local:
                return local[key]
            if key in known:
                return known[key][0], known[key][1]
            return None

        # Check each relationship: its evidence, both ends, and whether the verb's target type
        # matches.
        for r in data.get("relationships", []):
            src, dst, verb = resolve(r["from"]), resolve(r["to"]), r["verb"]
            if not evidenced(r["evidence"]):
                reject(f"{r['from']} --{verb}--> {r['to']}: evidence not found verbatim")
                continue
            if not src or not dst:
                reject(f"{r['from']} --{verb}--> {r['to']}: unknown endpoint")
                continue
            # A verb that names one target type (such as a person) must point at that type.
            target = self.v.verbs[verb].target
            if target and dst[1] != target:
                reject(
                    f"{r['from']} --{verb}--> {r['to']}: "
                    f"{verb} must point at {target}, not {dst[1]}"
                )
                continue
            # Attach the link to the page that owns the source end. Shared entities are handled
            # separately.
            if src[0] in pages_by_slug:
                pages_by_slug[src[0]].add_link(verb, dst[0])
            elif src[0] in self.x._globals:
                self.x._globals[src[0]].add_link(verb, dst[0])
            else:
                reject(f"{r['from']} --{verb}--> {r['to']}: source is not a page of this document")
                continue
            report["accepted"] += 1
