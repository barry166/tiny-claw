from __future__ import annotations

import pytest

from tiny_claw._internal.app import build_application
from tiny_claw._internal.errors import ConfigurationError
from tiny_claw._internal.settings import Settings


def test_application_supports_claude_alias(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "anthropic",
            "CLAUDE_KEY": "key",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    app = build_application(settings)

    assert app.engine.provider_name == "claude"


def test_application_rejects_missing_openai_key(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "openai",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    with pytest.raises(ConfigurationError, match="OPENAI"):
        build_application(settings)


def test_application_passes_openai_base_url(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "TINY_CLAW_PROVIDER": "openai",
            "OPENAI_KEY": "key",
            "OPENAI_BASE_URL": "https://openai.example/v1",
            "TINY_CLAW_STATE_DIR": str(tmp_path),
        }
    )

    app = build_application(settings)

    assert app.settings.openai_base_url == "https://openai.example/v1"


def test_application_registers_read_tool(tmp_path) -> None:
    settings = Settings.from_env({"TINY_CLAW_STATE_DIR": str(tmp_path)})

    app = build_application(settings)

    assert "read" in app.health().tools
