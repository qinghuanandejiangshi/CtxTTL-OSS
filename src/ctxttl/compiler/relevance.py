"""Dependency-free query relevance for structured context."""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ctxttl.models import ContextItem

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+", re.IGNORECASE)
_ENGLISH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "can",
        "did",
        "do",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "some",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "with",
        "you",
    }
)


def latest_user_query(messages: Sequence[Mapping[str, Any]]) -> str:
    """Extract text from the latest user message without changing provider payloads."""

    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") not in {"text", "input_text"}:
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            return "\n".join(parts)
        return ""
    return ""


class LexicalContextRelevanceScorer:
    """Score normalized lexical coverage with lightweight English and CJK tokenization."""

    def score(self, query: str, item: ContextItem) -> float:
        query_terms = _terms(query)
        if not query_terms:
            return 0.0
        document = f"{item.subject or ''} {json.dumps(item.value, ensure_ascii=False)}"
        overlap = query_terms & _terms(document)
        return len(overlap) / len(query_terms)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in _TOKEN_PATTERN.findall(text.casefold()):
        if _is_cjk(match):
            if len(match) == 1:
                terms.add(match)
            else:
                terms.update(match[index : index + 2] for index in range(len(match) - 1))
        elif match not in _ENGLISH_STOP_WORDS:
            terms.add(match)
    return terms


def _is_cjk(value: str) -> bool:
    return bool(value) and "\u3400" <= value[0] <= "\u9fff"
