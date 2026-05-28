from __future__ import annotations

from collections.abc import Mapping

import pytest

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.tools.registry import ToolRegistry, ToolResult


class FakeTool:
    @property
    def name(self) -> str:
        return "fake"

    @property
    def description(self) -> str:
        return "Fake tool for tests."

    def run(self, payload: Mapping[str, object]) -> ToolResult:
        return ToolResult(output=str(payload["message"]))


def test_tool_registry_registers_and_calls_tool() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool())

    result = registry.call("fake", {"message": "ok"})

    assert result.output == "ok"
    assert registry.names() == ("fake",)


def test_tool_registry_rejects_duplicates() -> None:
    registry = ToolRegistry()
    registry.register(FakeTool())

    with pytest.raises(ToolError, match="already registered"):
        registry.register(FakeTool())


def test_tool_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry()

    with pytest.raises(ToolError, match="Unknown tool"):
        registry.get("missing")
