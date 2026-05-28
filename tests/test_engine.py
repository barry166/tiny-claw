from __future__ import annotations

from tiny_claw._internal.context.builder import ContextBuilder
from tiny_claw._internal.engine.main_loop import MainLoop
from tiny_claw._internal.memory.file_store import FileMemoryStore
from tiny_claw._internal.provider.base import ModelRequest, ModelResponse
from tiny_claw._internal.tools.registry import ToolRegistry


class FakeProvider:
    @property
    def name(self) -> str:
        return "fake"

    def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.messages
        return ModelResponse(text="fake response", provider=self.name, model="fake-model")


def test_main_loop_accepts_injected_components(tmp_path) -> None:
    memory = FileMemoryStore(tmp_path)
    engine = MainLoop(
        provider=FakeProvider(),
        context_builder=ContextBuilder(),
        memory=memory,
        tools=ToolRegistry(),
    )

    result = engine.run(prompt="ping", max_steps=1)

    assert result.text == "fake response"
    assert result.provider == "fake"
    assert memory.read_recent(limit=2) == (
        "last_prompt: ping",
        "last_response: fake response",
    )
