# What this file does: the `eval` command. It ingests each answer key's document into a throwaway
# brain, then scores every stage (conversion, sections, tables, entities, attributes, links,
# storage, retrieval) against what the answer key says should be there, and writes the report.

"""Evaluation: ingest a document into a throwaway brain and score every stage against an
answer key (answer_keys/*.yaml).

Stages and their measures:

| Stage          | Measure                                                              |
|----------------|----------------------------------------------------------------------|
| conversion     | share of source words present in the markdown (from verification)    |
| classification | document kind right (accuracy 1 or 0)                                |
| document       | title found; document attributes right                               |
| sections       | precision, recall, F1 of section headings                            |
| tables         | tables found; share whose outcome (entities of which type) is right  |
| entities       | precision, recall, F1 (right page with the right type); type accuracy|
| attributes     | accuracy of attribute values on found entities                       |
| links          | precision, recall, F1 of typed links; links with the wrong verb      |
| storage        | share of storage checks passing (pages, content, chunks, links)      |
| retrieval      | share of questions whose expected page is in the top 5 (keyword)     |
| performance    | seconds for the whole run                                            |

Precision = right / produced; recall = right / expected; F1 = their harmonic mean;
accuracy = right / judged. The overall score is the mean of the stage scores in
``OVERALL``. Structural links (defined_in, section_of, mentions_item, appears_in) are not
scored; they are counted.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from docingest.utils import clock
from docingest.utils.logger import get_logger
from docingest.utils.gbrain_client import Reader
from docingest.ingestion.pipeline import Pipeline
from docingest.vocabulary.vocabulary_loader import Vocabulary

_log = get_logger(__name__)

# The answer key folder (answer_keys/), two levels above this file.
DEFAULT_ANSWERS = Path(__file__).resolve().parents[2] / "answer_keys"
# Links the rules write for document structure. They are counted in the report but not scored.
STRUCTURAL = {"defined_in", "section_of", "mentions_item", "appears_in", "attachment_of"}
# Page types that hold structure rather than facts, so they are left out of the entity score.
NOT_ENTITIES = {"source_document", "document_section"}
# Fields the pipeline writes for its own bookkeeping. They are not facts from the document, so they
# are not scored as attributes.
NOT_ATTRIBUTES = {"item_id", "doc_key", "doc_kind", "section", "title", "type"}
# The stages averaged into the overall score.
OVERALL = [
    "classification",
    "sections_f1",
    "tables_accuracy",
    "entities_f1",
    "attributes_accuracy",
    "links_f1",
    "storage_pass_rate",
    "retrieval_hit_rate",
]


# Normalise text for comparison: lower case, accents removed, punctuation turned into spaces.
def norm(text) -> str:
    """Lower case, accents and punctuation folded to spaces, single spaces."""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    t = "".join(c if c.isalnum() else " " for c in t)
    return re.sub(r"\s+", " ", t).strip()


# Two texts match when they are equal after normalising. A short text also matches when it appears
# whole inside the longer one. The four-character minimum stops tiny words from matching by
# accident.
def same_text(a, b) -> bool:
    """Equal after normalizing, or one contains the other (for long values)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= 4 and f" {short} " in f" {long_} "


# Precision (how many produced items were right), recall (how many expected items were found) and
# F1 (their balance). A value with no denominator is None.
def prf(right: int, produced: int, expected: int) -> dict:
    """Precision, recall and F1 as fractions (None when undefined)."""
    p = right / produced if produced else None
    r = right / expected if expected else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if expected else None)
    return {
        "precision": p,
        "recall": r,
        "f1": f,
        "right": right,
        "produced": produced,
        "expected": expected,
    }


# ---- what the pipeline produced ------------------------------------------------------


# Collect what the pipeline actually produced for this document: its entity pages, and the shared
# entities it contributed to.
def _actual_entities(m: dict, state: dict, key: str, vocab: Vocabulary) -> list[dict]:
    """Entity pages of the document plus the shared entities it contributed to."""
    by_gbrain = {t.gbrain_type: t.name for t in vocab.types.values()}
    out = []
    for p in m["pages"]:
        vtype = by_gbrain.get(p["type"], p["type"])
        if vtype in NOT_ENTITIES:
            continue
        item_id = p["fields"].get("item_id")
        # Titles of items read as "ID: name". Only the name part is compared with the answer key.
        name = p["title"].split(": ", 1)[1] if item_id and ": " in p["title"] else p["title"]
        out.append(
            {
                "slug": p["slug"],
                "type": vtype,
                "id": item_id,
                "name": name,
                "attributes": {k: v for k, v in p["fields"].items() if k not in NOT_ATTRIBUTES},
                "links": p["links"],
            }
        )
    # A shared entity counts for this document only when this document contributed to it.
    for slug, g in state.get("globals", {}).items():
        contrib = g.get("contributions", {}).get(key)
        if contrib is None:
            continue
        attrs = {
            k: v[-1] if isinstance(v, list) and v else v for k, v in contrib["attributes"].items()
        }
        out.append(
            {
                "slug": slug,
                "type": g["vocab_type"],
                "id": attrs.get("item_id"),
                "name": g["name"],
                "attributes": {k: v for k, v in attrs.items() if k not in NOT_ATTRIBUTES},
                "links": contrib.get("links", {}),
            }
        )
    return out


# Pair each expected entity with one produced entity, at most once each. Matching tries the ID
# first, then the exact name, then a loose name match within the same type.
def _match_entities(gold: list[dict], actual: list[dict]) -> dict[str, dict]:
    """Gold key -> the actual entity it matches (one to one, ID first, then name)."""
    used: set[str] = set()
    matches: dict[str, dict] = {}

    # Give the expected entity the first unused candidate in the pool. Candidates of the right type
    # come first.
    def take(g, pool):
        for a in pool:
            if a["slug"] not in used:
                used.add(a["slug"])
                matches[g["key"]] = a
                return True
        return False

    # Entities with an ID are matched by ID first, since that is the most reliable.
    for g in [g for g in gold if g.get("id")]:
        pool = [a for a in actual if a["id"] and a["id"].upper() == g["id"].upper()]
        take(g, sorted(pool, key=lambda a: a["type"] != g["type"]))
    # Entities without an ID are matched by name.
    named = [g for g in gold if not g.get("id")]
    # Exact names first (any type), then loose matches within the same type only, so a
    # long sentence that mentions "credit bureau" is not taken for the credit bureau.
    for g in named:
        exact = [a for a in actual if norm(a["name"]) == norm(g["name"])]
        take(g, sorted(exact, key=lambda a: a["type"] != g["type"]))
    # Loose matches are tried only for the entities the exact pass did not match.
    for g in [g for g in named if g["key"] not in matches]:
        take(g, [a for a in actual if a["type"] == g["type"] and same_text(a["name"], g["name"])])
    return matches


# ---- scoring -------------------------------------------------------------------------


# Score section headings: which expected headings were found, and which produced headings were not
# expected.
def _score_sections(gold: list[str], actual: list[str]) -> dict:
    ga, aa = [norm(h) for h in gold], [norm(h) for h in actual]
    # An expected heading counts as found when a produced heading matches it.
    found = [h for h in ga if any(h == a or same_text(h, a) for a in aa)]
    extra = [actual[i] for i, a in enumerate(aa) if not any(a == h or same_text(a, h) for h in ga)]
    res = prf(len(found), len(actual), len(gold))
    res["missing"] = [gold[i] for i, h in enumerate(ga) if h not in found]
    res["unexpected"] = extra
    return res


# Score tables. Each expected table is found by its first column, then its outcome (what it became)
# is compared with the expected outcome.
def _score_tables(gold: list[dict], actual: list[dict]) -> dict:
    remaining = list(actual)
    rows = []
    for g in gold:
        first = norm(g["first_column"])
        # Each produced table can be used once, so two expected tables cannot claim the same one.
        hit = next((a for a in remaining if a["header"] and norm(a["header"][0]) == first), None)
        if hit:
            remaining.remove(hit)
        became = hit["mapped_to"] if hit else None
        want = norm(g["becomes"])
        # Right when the outcome is exactly the expected one. Entity outcomes also count as right
        # when the expected type is among the types listed.
        ok = bool(became) and (
            norm(became) == want
            or (
                want.startswith("entities")
                and norm(became).startswith("entities")
                and any(norm(t) in norm(became) for t in g["becomes"].split(":", 1)[1].split(","))
            )
        )
        rows.append(
            {
                "first_column": g["first_column"],
                "expected": g["becomes"],
                "actual": became,
                "right": ok,
            }
        )
    found = sum(r["actual"] is not None for r in rows)
    right = sum(r["right"] for r in rows)
    return {
        "found": found,
        "expected": len(gold),
        "detection_rate": found / len(gold) if gold else None,
        "accuracy": right / len(gold) if gold else None,
        "rows": rows,
    }


# Score entities. An entity is right only when it matched an expected one and has the expected
# type. Also gives the results per type.
def _score_entities(gold: list[dict], actual: list[dict], matches: dict[str, dict]) -> dict:
    # Matched entities with the expected type.
    right = [
        k
        for k, a in matches.items()
        if a["type"] == next(g for g in gold if g["key"] == k)["type"]
    ]
    # Matched, but with the wrong type. These are reported on their own, since the entity was
    # found.
    wrong_type = [
        f"{k}: expected {next(g for g in gold if g['key'] == k)['type']}, got {a['type']}"
        for k, a in matches.items()
        if k not in right
    ]
    matched_slugs = {a["slug"] for a in matches.values()}
    res = prf(len(right), len(actual), len(gold))
    res["type_accuracy"] = len(right) / len(matches) if matches else None
    res["missing"] = [f"{g['key']} ({g['type']})" for g in gold if g["key"] not in matches]
    res["unexpected"] = [
        f"{a['slug']} ({a['type']})" for a in actual if a["slug"] not in matched_slugs
    ]
    res["wrong_type"] = wrong_type
    # Counts per entity type, for the per-type table in the report.
    per_type = defaultdict(lambda: {"expected": 0, "right": 0, "produced": 0})
    for g in gold:
        per_type[g["type"]]["expected"] += 1
        if g["key"] in right:
            per_type[g["type"]]["right"] += 1
    for a in actual:
        per_type[a["type"]]["produced"] += 1
    res["per_type"] = {
        t: {
            **v,
            **{
                k: x
                for k, x in prf(v["right"], v["produced"], v["expected"]).items()
                if k in ("precision", "recall", "f1")
            },
        }
        for t, v in sorted(per_type.items())
    }
    return res


# Score attribute values. Each expected value must appear under the same attribute name. A value
# found under another name is counted separately, so the report can show the naming gap.
def _score_attributes(
    gold: list[dict], matches: dict[str, dict], doc_fields: dict, doc_gold: dict
) -> dict:
    judged = right = elsewhere = 0
    misses = []
    pairs = [
        (g["key"], g.get("attributes", {}), matches[g["key"]]["attributes"])
        for g in gold
        if g["key"] in matches
    ]
    # The document's own attributes are judged the same way.
    pairs.append(("document", doc_gold, doc_fields))
    for key, want, have in pairs:
        for k, v in want.items():
            judged += 1
            # Right: the attribute holds the expected value under the expected name.
            if k in have and same_text(have[k], v):
                right += 1
            # Not under that name, but the value is present on the item under another attribute.
            elif any(same_text(x, v) for x in have.values() if isinstance(x, str | int | float)):
                elsewhere += 1
                misses.append(f"{key}.{k}: value found under another attribute name")
            else:
                misses.append(f"{key}.{k}: expected '{v}', got '{have.get(k, '(none)')}'")
    return {
        "accuracy": right / judged if judged else None,
        "right": right,
        "judged": judged,
        "under_other_name": elsewhere,
        "misses": misses,
    }


# Score links. Each produced link (source, verb, target) is compared with the expected links. A
# link with the right ends but the wrong verb is reported separately.
def _score_links(
    gold_links: list,
    matches: dict[str, dict],
    actual: list[dict],
    doc_page: dict,
    all_pages: list[dict],
    vocab: Vocabulary,
) -> dict:
    # Map each page back to its answer-key name, so produced links can be compared with the
    # expected ones.
    slug_to_key = {a["slug"]: k for k, a in matches.items()}
    slug_to_key[doc_page["slug"]] = "document"
    produced = set()
    structural = Counter()
    sources = [*actual, {"slug": doc_page["slug"], "links": doc_page["links"]}]
    # Every entity page of the document, and the document itself, contribute their links.
    for a in sources:
        src = slug_to_key.get(a["slug"], f"?{a['slug']}")
        for name, targets in a["links"].items():
            verb = vocab.canonical_verb(name)
            # Only the scored (non-structural) verbs are compared.
            if verb not in STRUCTURAL:
                produced |= {(src, verb, slug_to_key.get(t, f"?{t}")) for t in targets}
    for p in all_pages:
        for verb, targets in p["links"].items():
            if vocab.canonical_verb(verb) in STRUCTURAL:
                structural[vocab.canonical_verb(verb)] += len(targets)
    # Answer keys may use an earlier verb name, so the expected verbs are mapped the same way.
    expected = {(s, vocab.canonical_verb(v), t) for s, v, t in gold_links}
    right = produced & expected
    res = prf(len(right), len(produced), len(expected))
    # Index the produced links by their ends, so a link with the wrong verb can be spotted.
    pairs = {(s, t): v for s, v, t in produced}
    # An expected link whose ends were produced, but with a different verb.
    res["wrong_verb"] = [
        f"{s} -{pairs[(s, t)]}-> {t} (expected {v})"
        for s, v, t in expected - right
        if (s, t) in pairs
    ]
    res["missing"] = [f"{s} -{v}-> {t}" for s, v, t in sorted(expected - right)]
    res["unexpected"] = [f"{s} -{v}-> {t}" for s, v, t in sorted(produced - expected)]
    res["structural_counts"] = dict(structural)
    # Counts per verb: [right, produced, expected].
    per_verb = defaultdict(lambda: [0, 0, 0])
    for _, v, _ in expected:
        per_verb[v][2] += 1
    for _, v, _ in produced:
        per_verb[v][1] += 1
    for _, v, _ in right:
        per_verb[v][0] += 1
    res["per_verb"] = {v: prf(*c) for v, c in sorted(per_verb.items())}
    return res


# Score search. For each question, check whether an expected page is in the top 5 keyword results.
# An expectation can name a section as "section:heading".
def _score_retrieval(
    questions: list[dict], matches: dict[str, dict], sections: list[dict], reader: Reader
) -> dict:
    section_slug = {norm(s["heading"]): s["slug"] for s in sections}
    rows = []
    for q in questions:
        accept = set()
        for e in q["expect"]:
            if e.startswith("section:"):
                h = norm(e.split(":", 1)[1])
                # A section is accepted when its heading matches the named section.
                accept |= {slug for hh, slug in section_slug.items() if same_text(hh, h)}
            elif e in matches:
                accept.add(matches[e]["slug"])
        hits = [r.get("slug") for r in reader.search(q["query"], limit=5)]
        rows.append(
            {
                "query": q["query"],
                "hit": bool(accept & set(hits)),
                "top5": hits,
                "accept": sorted(accept),
            }
        )
    n = len(rows)
    return {"hit_rate": sum(r["hit"] for r in rows) / n if n else None, "questions": rows}


# ---- one run ---------------------------------------------------------------------------


# Run one answer key: ingest its document into a brain, verify it, score every stage, and return
# the result. The brain is a temporary folder unless keep is set.
def run(answer_path: Path, work: Path, keep: bool = False) -> dict:
    """Ingest the answer key's document into a new brain and score every stage."""
    _log.info("answer key started", key=answer_path.stem)
    ak = yaml.safe_load(answer_path.read_text())
    doc = (answer_path.parent / ak["document"]).resolve()
    vocab = Vocabulary()
    stamp = clock.compact()
    # With --keep, the brain and work files stay under work/eval. Otherwise they go to a temporary
    # folder that is deleted afterwards.
    root = (
        work / "eval" / f"{answer_path.stem}-{stamp}"
        if keep
        else Path(tempfile.mkdtemp(prefix="docingest-eval-")).resolve()
    )
    brain = root / "brain"
    # The throwaway brain is a temporary folder: it is removed after the scoring unless --keep.
    _log.info("throwaway brain", path=brain)
    ework = root / "work"
    started = time.time()
    try:
        # Run the real pipeline on the document, into the throwaway brain.
        pipe = Pipeline(ework, brain)
        result = pipe.ingest([doc])
        # A document that failed to ingest makes the evaluation impossible, so stop here.
        if not result["processed"]:
            raise RuntimeError(f"{doc} was not ingested: {result['failed']}")
        key = result["processed"][0]
        # Verification provides the storage results that are scored later.
        report = pipe.verify(result["processed"])
        seconds = round(time.time() - started, 1)
        m = json.loads((ework / "manifests" / f"{key}.json").read_text())
        state = json.loads((ework / "state.json").read_text())
        # This document's verification record, and its checks indexed by name.
        vdoc = next(d for d in report["documents"] if d["key"] == key)
        checks = {c["name"]: c for c in vdoc["checks"]}

        # Score each stage against the answer key, in the same order as the stage table in the file
        # header.
        actual = _actual_entities(m, state, key, vocab)
        matches = _match_entities(ak["entities"], actual)
        doc_page = m["pages"][0]
        sections = _score_sections(ak["sections"], [s["heading"] for s in m["sections"]])
        tables = _score_tables(ak["tables"], m["tables"])
        entities = _score_entities(ak["entities"], actual, matches)
        attributes = _score_attributes(
            ak["entities"], matches, doc_page["fields"], ak.get("document_attributes", {})
        )
        links = _score_links(ak["links"], matches, actual, doc_page, m["pages"], vocab)
        # Storage is judged by the verification checks that cover the stored pages.
        storage_names = [n for n in ("pages", "content", "chunks", "links") if n in checks]
        storage_pass = sum(checks[n]["status"] == "pass" for n in storage_names)
        # Retrieval needs the live brain, so the reader is opened after the pipeline has finished.
        with Reader(pipe.gb) as reader:
            retrieval = _score_retrieval(ak.get("questions", []), matches, m["sections"], reader)
        # The word coverage of the conversion is read back from the text of its verification
        # detail.
        coverage = re.search(
            r"([\d.]+)% of source words", checks.get("conversion", {}).get("detail", "")
        )
        stages = {
            "conversion": {
                "word_coverage": float(coverage.group(1)) / 100 if coverage else None,
                "status": checks.get("conversion", {}).get("status"),
                "detail": checks.get("conversion", {}).get("detail"),
            },
            # Did the pipeline name the right kind of document?
            "classification": {
                "expected": ak["kind"],
                "actual": m["kind"],
                "accuracy": float(m["kind"] == ak["kind"]),
            },
            "document": {
                "title": m["title"],
                "title_found": norm(ak.get("title_contains", "")) in norm(m["title"]),
            },
            "sections": sections,
            "tables": tables,
            "entities": entities,
            "attributes": attributes,
            "links": links,
            "storage": {
                "pass_rate": storage_pass / len(storage_names) if storage_names else None,
                "checks": {n: checks[n]["status"] for n in storage_names},
                "pages": vdoc["pages"],
                "chunks": vdoc["chunks"],
                "links_stored": vdoc["links"],
            },
            "retrieval": retrieval,
            "performance": {
                "seconds": seconds,
                "pages_per_second": round(vdoc["pages"] / seconds, 1) if seconds else None,
            },
        }
        # The numbers that make up the overall score, plus the overall score itself.
        summary = {
            "classification": stages["classification"]["accuracy"],
            "sections_f1": sections["f1"],
            "tables_accuracy": tables["accuracy"],
            "entities_f1": entities["f1"],
            "attributes_accuracy": attributes["accuracy"],
            "links_f1": links["f1"],
            "storage_pass_rate": stages["storage"]["pass_rate"],
            "retrieval_hit_rate": retrieval["hit_rate"],
        }
        # The overall score is the average of the stages that could be scored. A stage with no data
        # is left out.
        scored = [v for k, v in summary.items() if k in OVERALL and v is not None]
        summary["overall"] = sum(scored) / len(scored) if scored else None
        _log.step("answer key scored", key=answer_path.stem, overall=f"{(summary['overall'] or 0) * 100:.0f}%",
                  secs=f"{time.time() - started:.1f}")
        return {
            "answer_key": answer_path.stem,
            "answer_path": str(answer_path),
            "document": str(doc),
            "evaluated_at": clock.iso(),
            "vocabulary_version": vocab.pack["version"],
            "summary": summary,
            "stages": stages,
            "review_candidates": len(ak.get("review_candidates", [])),
            "kept_brain": str(brain) if keep else None,
        }
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


# The summary of one evaluation, in the form written to the run history for the dashboard.
def history_row(r: dict) -> dict:
    """The evaluation's summary for run_history/evals.jsonl."""
    return {
        "answer_key": r["answer_key"],
        "document": r["document"],
        "evaluated_at": r["evaluated_at"],
        "vocabulary_version": r["vocabulary_version"],
        **r["summary"],
        "entities_precision": r["stages"]["entities"]["precision"],
        "entities_recall": r["stages"]["entities"]["recall"],
        "links_precision": r["stages"]["links"]["precision"],
        "links_recall": r["stages"]["links"]["recall"],
        "seconds": r["stages"]["performance"]["seconds"],
    }


# ---- report --------------------------------------------------------------------------


# Format a fraction as a whole percentage, or "-" when there is no value.
def _pct(v) -> str:
    return "-" if v is None else f"{100 * v:.0f}%"


# A titled bullet list for the report. Long lists are cut at 40 items, with a count of the rest.
def _list(title: str, items: list[str], limit: int = 40) -> list[str]:
    if not items:
        return []
    out = ["", f"**{title}** ({len(items)}):", ""]
    out += [f"- {i}" for i in items[:limit]]
    if len(items) > limit:
        out.append(f"- ... and {len(items) - limit} more")
    return out


# Write one evaluation as markdown: the stage summary, the per-type and per-verb tables, the table
# and question results, then the list of misses.
def render(r: dict) -> str:
    """One evaluation as markdown."""
    s, st = r["summary"], r["stages"]
    e, lk, sec, tb, at, rt = (
        st[k] for k in ("entities", "links", "sections", "tables", "attributes", "retrieval")
    )
    out = [
        f"## {r['answer_key']} — overall {_pct(s['overall'])}",
        "",
        f"Document `{r['document']}`, evaluated {r['evaluated_at']}, vocabulary {r['vocabulary_version']}.",
        "",
        "| Stage | Measure | Result | Detail |",
        "|---|---|---|---|",
        f"| Conversion | Words kept | {_pct(st['conversion']['word_coverage'])} | {st['conversion']['detail']} |",
        f"| Classification | Accuracy | {_pct(st['classification']['accuracy'])} | expected {st['classification']['expected']}, got {st['classification']['actual']} |",
        f'| Document | Title found | {"yes" if st["document"]["title_found"] else "no"} | "{st["document"]["title"]}" |',
        f"| Sections | Precision / recall / F1 | {_pct(sec['precision'])} / {_pct(sec['recall'])} / **{_pct(sec['f1'])}** | {sec['right']} of {sec['expected']} expected found; {sec['produced']} produced |",
        f"| Tables | Found / accuracy | {_pct(tb['detection_rate'])} / **{_pct(tb['accuracy'])}** | {tb['found']} of {tb['expected']} found; accuracy = outcome right |",
        f"| Entities | Precision / recall / F1 | {_pct(e['precision'])} / {_pct(e['recall'])} / **{_pct(e['f1'])}** | {e['right']} right of {e['expected']} expected; {e['produced']} produced; type accuracy {_pct(e['type_accuracy'])} |",
        f"| Attributes | Accuracy | **{_pct(at['accuracy'])}** | {at['right']} of {at['judged']} values right; {at['under_other_name']} under another name |",
        f"| Links | Precision / recall / F1 | {_pct(lk['precision'])} / {_pct(lk['recall'])} / **{_pct(lk['f1'])}** | {lk['right']} right of {lk['expected']} expected; {lk['produced']} produced; {len(lk['wrong_verb'])} wrong verb |",
        f"| Storage | Checks passing | **{_pct(st['storage']['pass_rate'])}** | {st['storage']['pages']} pages, {st['storage']['chunks']} chunks, {st['storage']['links_stored']} links stored |",
        f"| Retrieval | Hit rate (top 5, keyword) | **{_pct(rt['hit_rate'])}** | {sum(q['hit'] for q in rt['questions'])} of {len(rt['questions'])} questions |",
        f"| Performance | Seconds | {st['performance']['seconds']} | {st['performance']['pages_per_second']} pages per second |",
        "",
        "### Entities by type",
        "",
        "| Type | Expected | Produced | Right | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for t, v in e["per_type"].items():
        out.append(
            f"| `{t}` | {v['expected']} | {v['produced']} | {v['right']} | {_pct(v['precision'])} | {_pct(v['recall'])} | {_pct(v['f1'])} |"
        )
    out += [
        "",
        "### Links by verb",
        "",
        "| Verb | Expected | Produced | Right | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for v, x in lk["per_verb"].items():
        out.append(
            f"| `{v}` | {x['expected']} | {x['produced']} | {x['right']} | {_pct(x['precision'])} | {_pct(x['recall'])} | {_pct(x['f1'])} |"
        )
    out += [
        "",
        f"Structural links (not scored): {', '.join(f'{k} {n}' for k, n in lk['structural_counts'].items()) or 'none'}.",
    ]
    out += ["", "### Tables", "", "| First column | Expected | Got | Right |", "|---|---|---|---|"]
    out += [
        f"| {t['first_column']} | {t['expected']} | {t['actual'] or 'not found'} | {'yes' if t['right'] else 'no'} |"
        for t in tb["rows"]
    ]
    out += ["", "### Retrieval", "", "| Question | Hit | Top result |", "|---|---|---|"]
    out += [
        f"| {q['query']} | {'yes' if q['hit'] else 'no'} | `{(q['top5'] or ['-'])[0]}` |"
        for q in rt["questions"]
    ]
    out += _list("Missing sections", sec["missing"])
    out += _list("Unexpected sections", sec["unexpected"])
    out += _list("Missing entities", e["missing"])
    out += _list("Entities with the wrong type", e["wrong_type"])
    out += _list("Unexpected entities", e["unexpected"])
    out += _list("Attribute misses", at["misses"])
    out += _list("Missing links", lk["missing"])
    out += _list("Links with the wrong verb", lk["wrong_verb"])
    out += _list("Unexpected links", lk["unexpected"])
    out += ["", f"{r['review_candidates']} review candidates in the answer key are not scored."]
    return "\n".join(out) + "\n"


# Write the combined report for all evaluations, as a timestamped copy and as the latest copy, in
# markdown and JSON.
def write_reports(reports: list[dict], work: Path) -> Path:
    """Write eval-<time>.md/.json and eval-latest.md/.json; returns the latest markdown path."""
    folder = work / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    head = [
        "# docingest evaluation",
        "",
        "Each answer key's document is ingested into a new, throwaway brain and every stage is "
        "scored. Precision = right / produced, recall = right / expected, F1 = their harmonic "
        "mean, accuracy = right / judged. Overall = mean of classification, sections F1, tables "
        "accuracy, entities F1, attributes accuracy, links F1, storage pass rate and retrieval "
        "hit rate.",
        "",
        "| Answer key | Overall | Entities F1 | Links F1 | Attributes | Retrieval |",
        "|---|---|---|---|---|---|",
    ]
    head += [
        f"| {r['answer_key']} | **{_pct(r['summary']['overall'])}** | {_pct(r['summary']['entities_f1'])} "
        f"| {_pct(r['summary']['links_f1'])} | {_pct(r['summary']['attributes_accuracy'])} "
        f"| {_pct(r['summary']['retrieval_hit_rate'])} |"
        for r in reports
    ]
    md = "\n".join(head) + "\n\n" + "\n".join(render(r) for r in reports)
    data = json.dumps(reports, indent=1, ensure_ascii=False)
    stamp = clock.compact()
    (folder / f"eval-{stamp}.md").write_text(md)
    (folder / f"eval-{stamp}.json").write_text(data)
    (folder / "eval-latest.md").write_text(md)
    (folder / "eval-latest.json").write_text(data)
    return folder / "eval-latest.md"
