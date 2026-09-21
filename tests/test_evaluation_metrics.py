"""Deterministic model answer metric tests."""

from ctxttl.evaluation import (
    normalize_answer,
    normalized_exact_match,
    normalized_reference_present,
)


def test_answer_normalization_is_unicode_case_and_whitespace_stable() -> None:
    assert normalize_answer("  ＰＯＳＴＧＲＥＳＱＬ\n") == "postgresql"
    assert normalized_exact_match("PostgreSQL", "postgresql") is True


def test_exact_match_does_not_hide_explanatory_or_punctuation_differences() -> None:
    assert normalized_exact_match("The answer is PostgreSQL.", "PostgreSQL") is False
    assert normalized_reference_present("The answer is PostgreSQL.", "PostgreSQL") is True
