"""Deterministic answer metrics that do not depend on an LLM judge."""

import json
import re
import unicodedata

from pydantic import JsonValue

_WHITESPACE = re.compile(r"\s+")


def normalize_answer(value: JsonValue | str) -> str:
    """Apply a deliberately conservative, language-neutral normalization."""

    text = (
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def normalized_exact_match(candidate: str, reference: JsonValue) -> bool:
    """Compare answers after Unicode, case, and whitespace normalization."""

    return normalize_answer(candidate) == normalize_answer(reference)


def normalized_reference_present(candidate: str, reference: JsonValue) -> bool:
    """Check whether a non-empty normalized reference is explicitly present in an answer."""

    normalized_reference = normalize_answer(reference)
    return bool(normalized_reference) and normalized_reference in normalize_answer(candidate)
