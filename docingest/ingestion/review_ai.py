# What this file does: asks the AI for a suggestion on each schema gap the rules could not
# settle (for example an unknown ID prefix). The suggestion is kept only if it passes the same
# checks as a reviewer's decision, so a person still approves it with one word.

"""AI suggestions for schema gaps the rules could not settle.

Runs only with --llm. The model reads a proposal (the gap, every affected row, the
vocabulary's types or verbs) and answers with one structured suggestion. A suggestion is
kept only if its target exists, it passes the same checks a reviewer's decision does, and its
evidence is quoted verbatim from the rows. It never applies itself: it fills the proposal so
a reviewer can approve with one word.
"""

from __future__ import annotations

import json

from docingest.ingestion.extract_with_ai import MODEL
from docingest.ingestion.review_proposals import ReviewBoard, ReviewError
from docingest.utils.markdown_parsing import normalize_ws
from docingest.vocabulary.vocabulary_loader import Vocabulary

# Instructions for the model. It must pick one action and quote its evidence from the rows.
INSTRUCTIONS = (
    "You help maintain the vocabulary of a document-ingestion system. A gap is something the "
    "vocabulary did not cover. Pick exactly one action:\n"
    "- map_to_existing: the rows or column mean an existing type (for an ID prefix) or verb "
    "(for a column); set `target` to its exact name.\n"
    "- new_type: only for an ID prefix whose rows are a new kind of thing; set `name` "
    "(lower_snake_case, singular).\n"
    "- leave_generic: nothing fits clearly. Prefer this over a doubtful choice.\n"
    "`evidence` must be an exact, contiguous quote copied from the rows. Never invent names."
)


# JSON schema for the model's answer, so the reply can only contain these fields and actions.
def _schema(board_vocab: Vocabulary) -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["map_to_existing", "new_type", "leave_generic"]},
            "target": {"type": "string"},
            "name": {"type": "string"},
            "reason": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["action", "target", "name", "reason", "evidence"],
        "additionalProperties": False,
    }


# Build the question for one proposal: the gap, the options the vocabulary allows, and the rows.
def _prompt(p: dict, vocab: Vocabulary) -> str:
    if p["kind"] == "new_type":
        options = "\n".join(
            f"- {t.name}: {t.description}" for t in vocab.types.values() if t.scope == "document" and t.name != "record"
        )
        gap = f"ID prefix {p['prefix']}- under the heading \"{p['heading']}\""
        rows = "\n".join(" | ".join(f"{k}: {v}" for k, v in r["cells"].items()) for r in p["rows"])
    else:
        # The type pairs the rows link. The verbs allowed between those pairs are the options
        # offered.
        pairs = {(r["type"], d) for r in p["rows"] for _, d in r["targets"]}
        names = sorted({v for s, d in pairs for v in vocab.verbs_between(s, d)})
        options = "\n".join(f"- {v}: {vocab.verbs[v].description}" for v in names)
        gap = f"column \"{p['column']}\" under the heading \"{p['heading']}\""
        rows = "\n".join(f"{r['id']} ({r['type']}) {p['column']}: {r['cell']}" for r in p["rows"])
    return f"Gap: {gap}\n\nExisting options:\n{options or '(none)'}\n\nRows:\n{rows}\n"


# Get one validated suggestion for a proposal. Returns None when the answer is unusable.
def suggest(client, vocab: Vocabulary, p: dict, effort: str = "medium") -> dict | None:
    """One validated suggestion for a proposal, or None when the answer is unusable."""
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=INSTRUCTIONS,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": _schema(vocab)}},
        messages=[{"role": "user", "content": _prompt(p, vocab)}],
    )
    # Take the text part of the reply, which is the JSON answer.
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    # The rows are the only place evidence may be quoted from.
    haystack = normalize_ws(_prompt(p, vocab).split("Rows:", 1)[1]).lower()
    quote = normalize_ws(data["evidence"]).lower()
    # Reject the suggestion unless the evidence is a verbatim quote from the rows.
    if data["action"] == "leave_generic" or not quote or quote not in haystack:
        return None
    # Record who suggested the action, why, and the quote that supports it.
    action = {"by": "ai", "reason": data["reason"], "evidence": data["evidence"].strip()}
    if data["action"] == "new_type" and p["kind"] == "new_type":
        action |= {"action": "new_type", "name": data["name"], "prefixes": [p["prefix"]],
                   "dir": f"docs/learned/{data['name'].replace('_', '-')}"}  # fmt: skip
    elif data["action"] == "map_to_existing":
        action |= {"action": "map_type" if p["kind"] == "new_type" else "alias", "target": data["target"]}
    else:
        return None
    return action


# Add a suggestion to each proposal that gets a valid one. Returns how many did.
def suggest_all(board: ReviewBoard, proposals: dict[str, dict], vocab: Vocabulary, effort: str = "medium", client=None) -> int:
    """Add a suggestion to each proposal that gets a valid one; returns how many did."""
    import anthropic

    # Use the client passed in (tests pass a fake one), or create the real one.
    client = client or anthropic.Anthropic(max_retries=4)
    added = 0
    for pid, p in proposals.items():
        try:
            # Ask for a suggestion. A failed API call, a rejected suggestion or a bad answer skips
            # only this proposal; the others still get theirs.
            s = suggest(client, vocab, p, effort)
            if s:
                board.build(p, s, vocab, board.learned())  # same checks as a reviewer's decision
        except (anthropic.APIError, ReviewError, KeyError):
            continue
        if s:
            # Attach the suggestion to the proposal and mark it as the chosen action, ready for a
            # reviewer.
            p["ai_suggestion"] = s
            board._choose(p)
            board.save(p)
            board.log("ai_suggestion", proposal=pid, action=s)
            added += 1
    return added
