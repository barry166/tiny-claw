from __future__ import annotations

from typing import Any

import pytest

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.registry import ToolRegistry


class FakeTool:
    @property
    def name(self) -> str:
        return "fake"

    @property
    def description(self) -> str:
        return "Fake tool for tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        return ToolOutput(content=str(input.arguments["message"]))


def test_tool_registry_registers_and_calls_tool() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool())

    result = registry.call("fake", {"message": "ok"})

    assert result.output == "ok"
    assert registry.names() == ("fake",)
    assert registry.definitions() == (FakeTool().definition(),)


def test_tool_registry_rejects_duplicates() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool())

    with pytest.raises(ToolError, match="already registered"):
        registry.register(FakeTool())


def test_tool_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolError, match="Unknown tool"):
        registry.get("missing")
