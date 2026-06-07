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


class ErroringTool:
    def __init__(self, *, name: str, error: str):
        self._name = name
        self.error = error
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} erroring tool for executor tests."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "command": {"type": "string"},
                "old_text": {"type": "string"},
            },
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    def run(self, input: ToolInput) -> ToolOutput:
        self.calls += 1
        raise ToolError(self.error)


class ErrorOutputTool(ErroringTool):
    def run(self, input: ToolInput) -> ToolOutput:
        self.calls += 1
        return ToolOutput(content=self.error, is_error=True)


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
    assert "read path does not exist: missing.txt" in observations[1].content
    assert "下一步建议" in observations[1].content
    assert observations[1].metadata["is_error"] is True


def test_tool_executor_rejects_invalid_parallel_limit() -> None:
    with pytest.raises(ValueError, match="max_parallel_tools"):
        ToolExecutor(tools=ToolRegistry(), max_parallel_tools=0)


def test_tool_executor_suggests_ls_for_missing_read_when_bash_is_visible() -> None:
    registry = ToolRegistry()
    registry.register(ErroringTool(name="read", error="read path does not exist: src/missing.py"))
    registry.register(RecordingTool(name="bash", events=[]))
    executor = ToolExecutor(tools=registry, visible_tool_names=("read", "bash"))

    observations = executor.run_tool_calls(
        (ToolCall(id="call-1", name="read", arguments={"path": "src/missing.py"}),)
    )

    observation = observations[0]
    assert observation.metadata["is_error"] is True
    assert observation.metadata["error_type"] == "read_path_not_found"
    assert observation.metadata["retryable"] is True
    assert observation.metadata["suggested_tool"] == "bash"
    assert "ls src" in observation.content


def test_tool_executor_does_not_suggest_bash_when_not_visible() -> None:
    registry = ToolRegistry()
    registry.register(ErroringTool(name="read", error="read path does not exist: src/missing.py"))
    registry.register(RecordingTool(name="bash", events=[]))
    executor = ToolExecutor(tools=registry, visible_tool_names=("read",))

    observations = executor.run_tool_calls(
        (ToolCall(id="call-1", name="read", arguments={"path": "src/missing.py"}),)
    )

    observation = observations[0]
    assert observation.metadata["is_error"] is True
    assert observation.metadata["error_type"] == "read_path_not_found"
    assert "当前没有可见的 bash 工具" in observation.content
    assert "ls src" not in observation.content
    assert "suggested_tool" not in observation.metadata


def test_tool_executor_suggests_read_for_edit_old_text_failure() -> None:
    registry = ToolRegistry()
    registry.register(
        ErroringTool(
            name="edit",
            error="edit tool could not find old_text in src/app.py",
        )
    )
    executor = ToolExecutor(tools=registry, visible_tool_names=("read", "edit"))

    observations = executor.run_tool_calls(
        (
            ToolCall(
                id="call-1",
                name="edit",
                arguments={"path": "src/app.py", "old_text": "old", "new_text": "new"},
            ),
        )
    )

    observation = observations[0]
    assert observation.metadata["error_type"] == "edit_old_text_not_found"
    assert observation.metadata["suggested_tool"] == "read"
    assert "先调用 read" in observation.content


def test_tool_executor_does_not_suggest_background_for_test_timeout() -> None:
    registry = ToolRegistry()
    registry.register(
        ErrorOutputTool(
            name="bash",
            error=(
                "command=uv run pytest\n"
                "cwd=/tmp/project\n"
                "timeout_seconds=1\n"
                "error=command timed out"
            ),
        )
    )
    executor = ToolExecutor(tools=registry, visible_tool_names=("bash",))

    observations = executor.run_tool_calls(
        (ToolCall(id="call-1", name="bash", arguments={"command": "uv run pytest"}),)
    )

    observation = observations[0]
    assert observation.metadata["error_type"] == "bash_timeout"
    assert "缩小执行范围" in observation.content
    assert "不要直接后台运行测试或构建" in observation.content


def test_tool_executor_suggests_background_for_service_timeout() -> None:
    registry = ToolRegistry()
    registry.register(
        ErrorOutputTool(
            name="bash",
            error=(
                "command=uv run tiny-claw serve --port 8000\n"
                "cwd=/tmp/project\n"
                "timeout_seconds=1\n"
                "error=command timed out"
            ),
        )
    )
    executor = ToolExecutor(tools=registry, visible_tool_names=("bash",))

    observations = executor.run_tool_calls(
        (
            ToolCall(
                id="call-1",
                name="bash",
                arguments={"command": "uv run tiny-claw serve --port 8000"},
            ),
        )
    )

    observation = observations[0]
    assert observation.metadata["error_type"] == "bash_timeout_service"
    assert "后台运行" in observation.content
    assert "日志文件" in observation.content


def test_tool_executor_blocks_repeated_identical_failures() -> None:
    tool = ErroringTool(name="read", error="read path does not exist: missing.txt")
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(tools=registry, visible_tool_names=("read",))
    call = ToolCall(id="call-1", name="read", arguments={"path": "missing.txt"})

    first = executor.run_tool_calls((call,))[0]
    second = executor.run_tool_calls((call,))[0]
    third = executor.run_tool_calls((call,))[0]

    assert first.metadata["error_type"] == "read_path_not_found"
    assert first.metadata["attempt"] == 1
    assert second.metadata["error_type"] == "read_path_not_found"
    assert second.metadata["attempt"] == 2
    assert "重复提醒" in second.content
    assert third.metadata["error_type"] == "repeat_call_blocked"
    assert third.metadata["retryable"] is False
    assert third.metadata["attempt"] == 3
    assert "已阻止继续重复执行" in third.content
    assert tool.calls == 2


def test_tool_executor_reports_unknown_visible_tool() -> None:
    registry = ToolRegistry()
    registry.register(RecordingTool(name="read", events=[]))
    executor = ToolExecutor(tools=registry, visible_tool_names=("read",))

    observations = executor.run_tool_calls(
        (ToolCall(id="call-1", name="fake_tool", arguments={"path": "x"}),)
    )

    observation = observations[0]
    assert observation.metadata["error_type"] == "unknown_tool"
    assert "只能使用当前可见工具：read" in observation.content
