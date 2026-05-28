"""OpenAI provider adapter placeholder.

The framework keeps this module dependency-free. Wire a concrete SDK client here
when the project is ready to add an OpenAI runtime dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from tiny_claw._internal.errors import ConfigurationError, ProviderError
from tiny_claw._internal.provider.base import LLMRequest, LLMResponse


@dataclass(frozen=True)
class OpenAIProvider:
    api_key: str | None
    model: str

    def __post_init__(self) -> None:
        if not self.api_key:
            raise ConfigurationError("TINY_CLAW_OPENAI_API_KEY is required for the openai provider")

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, _request: LLMRequest) -> LLMResponse:
        raise ProviderError(
            "OpenAI provider adapter is reserved for future SDK integration; "
            "use TINY_CLAW_PROVIDER=echo for the current no-dependency skeleton"
        )
