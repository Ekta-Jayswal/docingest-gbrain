"""Classify a converted document: brd, prd, sow, email, transcript, chat, ..."""

from __future__ import annotations

import re

from .mdutil import headings
from .model import Converted
from .vocab import Vocabulary, norm_header


def classify(conv: Converted, vocab: Vocabulary, override: str | None = None) -> str:
    """Return the document kind: the override, the converter's hint, or the best keyword match."""
    if override:
        return override
    if conv.kind_hint:
        return conv.kind_hint
    md = conv.markdown
    heading_text = " | ".join(norm_header(h) for _, _, h in headings(md))
    head = norm_header(md[:4000])
    scores: dict[str, int] = {}
    for kind, phrases in vocab.document_kinds.items():
        score = 0
        for phrase in phrases:
            pattern = re.compile(rf"\b{re.escape(norm_header(phrase))}\b")
            score += 3 * len(pattern.findall(heading_text)) + len(pattern.findall(head))
        scores[kind] = score
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else "document"
