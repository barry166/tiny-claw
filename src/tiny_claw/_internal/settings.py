"""Runtime settings for tiny-claw."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from tiny_claw._internal.errors import ConfigurationError

DEFAULT_STATE_DIR = Path.home() / ".tiny-claw"
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    provider_name: str = "echo"
    model: str = "echo"
    state_dir: Path = DEFAULT_STATE_DIR
    workdir: Path = field(default_factory=lambda: Path.cwd().resolve())
    openai_api_key: str | None = None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        log_level: str | None = None,
    ) -> Self:
        env = os.environ if environ is None else environ
        resolved_log_level = log_level or env.get("TINY_CLAW_LOG_LEVEL", "INFO")
        provider_name = env.get("TINY_CLAW_PROVIDER", "echo").lower()
        model = env.get("TINY_CLAW_MODEL", provider_name)
        state_dir = Path(env.get("TINY_CLAW_STATE_DIR", str(DEFAULT_STATE_DIR))).expanduser()
        workdir = Path(env.get("TINY_CLAW_WORKDIR", str(Path.cwd()))).expanduser().resolve()

        return cls(
            log_level=_normalize_log_level(resolved_log_level),
            provider_name=provider_name,
            model=model,
            state_dir=state_dir,
            workdir=workdir,
            openai_api_key=env.get("TINY_CLAW_OPENAI_API_KEY"),
        )


def _normalize_log_level(value: str) -> str:
    normalized = value.upper()
    if normalized not in LOG_LEVELS:
        raise ConfigurationError(
            f"Invalid log level {value!r}; expected one of {', '.join(sorted(LOG_LEVELS))}"
        )
    return normalized
