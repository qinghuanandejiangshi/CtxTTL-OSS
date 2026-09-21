import pytest
from pydantic import ValidationError

from ctxttl.config import MissingSessionBehavior, Settings


def test_settings_load_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTXTTL_PORT", "9001")
    monkeypatch.setenv("CTXTTL_MISSING_SESSION_BEHAVIOR", "passthrough")

    settings = Settings(_env_file=None)

    assert settings.port == 9001
    assert settings.missing_session_behavior == MissingSessionBehavior.PASSTHROUGH


def test_upstream_url_is_normalized() -> None:
    settings = Settings(upstream_base_url="http://localhost:11434/v1/", _env_file=None)

    assert settings.upstream_base_url == "http://localhost:11434/v1"


def test_target_budget_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError, match="target_context_tokens"):
        Settings(target_context_tokens=101, max_context_tokens=100, _env_file=None)


def test_connect_timeout_cannot_exceed_total_timeout() -> None:
    with pytest.raises(ValidationError, match="upstream_connect_timeout_seconds"):
        Settings(
            upstream_connect_timeout_seconds=11,
            upstream_timeout_seconds=10,
            _env_file=None,
        )


def test_empty_upstream_api_key_is_treated_as_unset() -> None:
    settings = Settings(upstream_api_key="", _env_file=None)

    assert settings.upstream_api_key is None


def test_archive_retention_defaults_to_thirty_days_and_can_be_unset() -> None:
    assert Settings(_env_file=None).archive_retention_days == 30
    assert Settings(archive_retention_days="", _env_file=None).archive_retention_days is None


def test_history_reference_limit_cannot_be_lower_than_normal_limit() -> None:
    with pytest.raises(ValidationError, match="history_reference_retrieval_limit"):
        Settings(
            history_retrieval_limit=5,
            history_reference_retrieval_limit=4,
            _env_file=None,
        )
