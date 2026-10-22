# What this file does: decides what kind of document a file is (BRD, PRD, SOW, email, ...).
# The kind chooses the vocabulary and the extraction rules that apply.

"""Classify a converted document: brd, prd, sow, email, transcript, chat, ..."""

from __future__ import annotations

import re

from docingest.utils.markdown_parsing import headings
from docingest.utils.data_types import Converted
from docingest.vocabulary.vocabulary_loader import Vocabulary, norm_header


# Decide the document kind. Order: a kind given on the command line wins, then the
# converter's hint, then the best keyword match. Falls back to "document" when no kind
# scores at least 3.
def classify(conv: Converted, vocab: Vocabulary, override: str | None = None) -> str:
    """Return the document kind: the override, the converter's hint, or the best keyword match."""
    if override:
        return override
    if conv.kind_hint:
        return conv.kind_hint
    md = conv.markdown
    # Keywords in headings count three times as much as keywords in the first 4000 characters.
    heading_text = " | ".join(norm_header(h) for _, _, h in headings(md))
    head = norm_header(md[:4000])
    scores: dict[str, int] = {}
    for kind, phrases in vocab.document_kinds.items():
        score = 0
        for phrase in phrases:
            # Match each phrase as whole words only, so "sow" does not match inside another word.
            pattern = re.compile(rf"\b{re.escape(norm_header(phrase))}\b")
            score += 3 * len(pattern.findall(heading_text)) + len(pattern.findall(head))
        scores[kind] = score
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "document"
