"""Core application main loop."""

from __future__ import annotations

from dataclasses import dataclass

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import LLMProvider, LLMRequest
from tiny_claw._internal.tools.registry import ToolRegistry


@dataclass(frozen=True)
class RunResult:
    text: str
    provider: str
    steps: int


@dataclass(frozen=True)
class MainLoop:
    provider: LLMProvider
    context_builder: ContextBuilder
    memory: FileMemoryStore
    tools: ToolRegistry

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def run(self, *, prompt: str, max_steps: int = 1) -> RunResult:
        recent_memory = self.memory.read_recent(limit=5)
        context = self.context_builder.build(prompt=prompt, memories=recent_memory)
        response = self.provider.complete(
            LLMRequest(
                messages=context.messages,
                tools=self.tools.definitions(),
                max_steps=max_steps,
            )
        )
        self.memory.append("last_prompt", prompt)
        self.memory.append("last_response", response.text)
        return RunResult(text=response.text, provider=response.provider, steps=1)
