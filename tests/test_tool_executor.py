from __future__ import annotations

import time
from collections.abc import Mapping
from threading import Barrier
from typing import Any

import pytest

from tiny_claw._internal.engine.tool_executor import ToolExecutor
from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import Role, ToolCall, ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin.read import ReadTool
from tiny_claw._internal.tools.builtin.write import WriteTool
from tiny_claw._internal.tools.registry import ToolRegistry


class SlowReadTool:
    def __init__(
        self,
        *,
        barrier: Barrier | None = None,
        delays: Mapping[str, float] | None = None,
    ):
        self.barrier = barrier
        self.delays = dict(delays or {})

    @property
    def name(self) -> str:
        return "read"

    @property
    def description(self) -> str:
        return "Slow read tool for executor tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        path = str(input.arguments["path"])
        if self.barrier is not None:
            self.barrier.wait(timeout=1)
        time.sleep(self.delays.get(path, 0))
        if path == "missing.txt":
            raise ToolError("read path does not exist: missing.txt")
        return ToolOutput(content=f"read:{path}")


class RecordingTool:
    def __init__(self, *, name: str, events: list[str]):
        self._name = name
        self.events = events

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} tool for executor tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        path = str(input.arguments["path"])
        self.events.append(f"{self.name}:{path}")
        return ToolOutput(content=f"{self.name}:{path}")


def test_tool_executor_runs_consecutive_reads_concurrently() -> None:
    registry = ToolRegistry()
    registry.register(SlowReadTool(barrier=Barrier(2), delays={"a.txt": 0.05, "b.txt": 0.05}))
    executor = ToolExecutor(tools=registry)

    started_at = time.perf_counter()
    observations = executor.run_tool_calls(
        (
            ToolCall(id="call-a", name="read", arguments={"path": "a.txt"}),
            ToolCall(id="call-b", name="read", arguments={"path": "b.txt"}),
        )
    )
    elapsed = time.perf_counter() - started_at

    assert elapsed < 0.5
    assert [message.content for message in observations] == ["read:a.txt", "read:b.txt"]


def test_tool_executor_preserves_original_order_for_parallel_reads() -> None:
    registry = ToolRegistry()
    registry.register(SlowReadTool(delays={"a.txt": 0.05, "b.txt": 0}))
    executor = ToolExecutor(tools=registry)

    observations = executor.run_tool_calls(
        (
            ToolCall(id="call-a", name="read", arguments={"path": "a.txt"}),
            ToolCall(id="call-b", name="read", arguments={"path": "b.txt"}),
        )
    )

    assert [message.tool_call_id for message in observations] == ["call-a", "call-b"]
    assert [message.content for message in observations] == ["read:a.txt", "read:b.txt"]


def test_tool_executor_uses_non_read_tools_as_ordered_barriers() -> None:
    events: list[str] = []
    registry = ToolRegistry()
    registry.register(RecordingTool(name="read", events=events))
    registry.register(RecordingTool(name="write", events=events))
    executor = ToolExecutor(tools=registry)

    observations = executor.run_tool_calls(
        (
            ToolCall(id="call-read-old", name="read", arguments={"path": "notes.txt"}),
            ToolCall(
                id="call-write",
                name="write",
                arguments={"path": "notes.txt", "content": "new"},
            ),
            ToolCall(id="call-read-new", name="read", arguments={"path": "notes.txt"}),
        )
    )

    assert events == ["read:notes.txt", "write:notes.txt", "read:notes.txt"]
    assert [message.tool_call_id for message in observations] == [
        "call-read-old",
        "call-write",
        "call-read-new",
    ]


def test_tool_executor_read_write_read_observes_updated_file(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(ReadTool(root=tmp_path))
    registry.register(WriteTool(root=tmp_path))
    executor = ToolExecutor(tools=registry)

    observations = executor.run_tool_calls(
        (
            ToolCall(id="call-read-old", name="read", arguments={"path": "notes.txt"}),
            ToolCall(
                id="call-write",
                name="write",
                arguments={"path": "notes.txt", "content": "new\n", "mode": "overwrite"},
            ),
            ToolCall(id="call-read-new", name="read", arguments={"path": "notes.txt"}),
        )
    )

    assert "1: old" in observations[0].content
    assert "mode=overwrite" in observations[1].content
    assert "1: new" in observations[2].content


def test_tool_executor_isolates_errors_in_parallel_read_group() -> None:
    registry = ToolRegistry()
    registry.register(SlowReadTool())
    executor = ToolExecutor(tools=registry)

    observations = executor.run_tool_calls(
        (
            ToolCall(id="call-ok", name="read", arguments={"path": "ok.txt"}),
            ToolCall(id="call-missing", name="read", arguments={"path": "missing.txt"}),
        )
    )

    assert len(observations) == 2
    assert observations[0].role is Role.TOOL
    assert observations[0].content == "read:ok.txt"
    assert observations[0].metadata["is_error"] is False
    assert observations[1].role is Role.TOOL
    assert observations[1].content == "read path does not exist: missing.txt"
    assert observations[1].metadata["is_error"] is True


def test_tool_executor_rejects_invalid_parallel_limit() -> None:
    with pytest.raises(ValueError, match="max_parallel_tools"):
        ToolExecutor(tools=ToolRegistry(), max_parallel_tools=0)
