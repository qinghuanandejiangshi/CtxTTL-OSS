import pytest
from pydantic import ValidationError

from ctxttl_mcp.config import MCPSettings


def test_loopback_bind_can_run_without_static_bearer() -> None:
    settings = MCPSettings(host="127.0.0.1", bearer_token=None, _env_file=None)

    assert settings.bearer_token is None


def test_non_loopback_bind_requires_static_bearer() -> None:
    with pytest.raises(ValidationError, match="bearer_token is required"):
        MCPSettings(host="0.0.0.0", bearer_token=None, _env_file=None)


def test_non_loopback_bind_accepts_configured_bearer() -> None:
    settings = MCPSettings(host="0.0.0.0", bearer_token="configured-secret", _env_file=None)

    assert settings.bearer_token is not None
