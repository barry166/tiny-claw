from __future__ import annotations

from typing import Any

import pytest

from tiny_claw._internal.errors import ToolError
from tiny_claw._internal.schema.message import ToolDefinition
from tiny_claw._internal.tools.base import ToolInput, ToolOutput
from tiny_claw._internal.tools.builtin.bash import BashTool
from tiny_claw._internal.tools.builtin.edit import EditTool
from tiny_claw._internal.tools.builtin.read import ReadTool
from tiny_claw._internal.tools.builtin.write import WriteTool
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


def test_bash_tool_runs_inside_workdir(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path)

    result = tool.run(ToolInput(arguments={"command": "pwd"}))

    assert result.output.startswith("command=pwd")
    assert f"cwd={tmp_path}" in result.output
    assert "exit_code=0" in result.output
    assert str(tmp_path) in result.output
    assert result.is_error is False


def test_bash_tool_default_timeout_is_60_seconds(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path)

    assert tool.timeout_seconds == 60
    assert tool.parameters["properties"]["timeout_seconds"]["default"] == 60


def test_bash_tool_runs_inside_relative_cwd(tmp_path) -> None:
    (tmp_path / "nested").mkdir()
    tool = BashTool(workdir=tmp_path)

    result = tool.run(ToolInput(arguments={"command": "pwd", "cwd": "nested"}))

    assert result.output.startswith("command=pwd")
    assert f"cwd={tmp_path / 'nested'}" in result.output


def test_bash_tool_rejects_cwd_outside_workdir(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path)

    with pytest.raises(ToolError, match="under the configured root"):
        tool.run(ToolInput(arguments={"command": "pwd", "cwd": "../outside"}))


def test_bash_tool_rejects_timeout_above_limit(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path)

    with pytest.raises(ToolError, match="cannot exceed 60"):
        tool.run(ToolInput(arguments={"command": "pwd", "timeout_seconds": 61}))


def test_bash_tool_returns_non_zero_exit_as_error(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path)

    result = tool.run(ToolInput(arguments={"command": "ls missing-file"}))

    assert result.is_error is True
    assert "exit_code=" in result.output
    assert "stderr:" in result.output
    assert "missing-file" in result.output


def test_bash_tool_returns_timeout_as_error(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path)

    result = tool.run(ToolInput(arguments={"command": "sleep 2", "timeout_seconds": 1}))

    assert result.is_error is True
    assert "error=command timed out" in result.output
    assert "timeout_seconds=1" in result.output


def test_bash_tool_truncates_long_output(tmp_path) -> None:
    tool = BashTool(workdir=tmp_path, max_output_chars=10)

    result = tool.run(ToolInput(arguments={"command": "printf 'abcdefghijklmnopqrstuvwxyz'"}))

    assert result.is_error is False
    assert "stdout_truncated=True" in result.output
    assert "<truncated" in result.output


def test_edit_tool_rejects_paths_outside_root(tmp_path) -> None:
    tool = EditTool(root=tmp_path, enabled=True)

    with pytest.raises(ToolError, match="under the configured root"):
        tool.run(ToolInput(arguments={"path": "../outside.txt", "content": "nope"}))


def test_write_tool_writes_file_inside_root(tmp_path) -> None:
    tool = WriteTool(root=tmp_path)

    result = tool.run(ToolInput(arguments={"path": "nested/notes.txt", "content": "hello\n"}))

    assert (tmp_path / "nested" / "notes.txt").read_text(encoding="utf-8") == "hello\n"
    assert result.output == "path=nested/notes.txt\nmode=overwrite\nbytes=6\nlines=1"


def test_write_tool_appends_file_inside_root(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("one\n", encoding="utf-8")
    tool = WriteTool(root=tmp_path)

    result = tool.run(
        ToolInput(arguments={"path": "notes.txt", "content": "two\n", "mode": "append"})
    )

    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one\ntwo\n"
    assert "mode=append" in result.output


def test_write_tool_rejects_paths_outside_root(tmp_path) -> None:
    tool = WriteTool(root=tmp_path)

    with pytest.raises(ToolError, match="under the configured root"):
        tool.run(ToolInput(arguments={"path": "../outside.txt", "content": "nope"}))


def test_write_tool_rejects_absolute_path(tmp_path) -> None:
    tool = WriteTool(root=tmp_path)

    with pytest.raises(ToolError, match="relative path"):
        tool.run(ToolInput(arguments={"path": str(tmp_path / "notes.txt"), "content": "nope"}))


def test_write_tool_rejects_directory_path(tmp_path) -> None:
    (tmp_path / "folder").mkdir()
    tool = WriteTool(root=tmp_path)

    with pytest.raises(ToolError, match="directory"):
        tool.run(ToolInput(arguments={"path": "folder", "content": "nope"}))


def test_write_tool_rejects_invalid_mode(tmp_path) -> None:
    tool = WriteTool(root=tmp_path)

    with pytest.raises(ToolError, match="mode"):
        tool.run(ToolInput(arguments={"path": "notes.txt", "content": "nope", "mode": "bad"}))


def test_read_tool_reads_file_inside_root(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    tool = ReadTool(root=tmp_path)

    result = tool.run(ToolInput(arguments={"path": "notes.txt"}))

    assert result.output == "1: alpha\n2: beta"


def test_read_tool_respects_line_range(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    tool = ReadTool(root=tmp_path)

    result = tool.run(ToolInput(arguments={"path": "notes.txt", "start_line": 2, "max_lines": 2}))

    assert result.output == "2: two\n3: three"


def test_read_tool_rejects_paths_outside_root(tmp_path) -> None:
    tool = ReadTool(root=tmp_path)

    with pytest.raises(ToolError, match="under the configured root"):
        tool.run(ToolInput(arguments={"path": "../outside.txt"}))


def test_read_tool_rejects_directory_path(tmp_path) -> None:
    (tmp_path / "folder").mkdir()
    tool = ReadTool(root=tmp_path)

    with pytest.raises(ToolError, match="directory"):
        tool.run(ToolInput(arguments={"path": "folder"}))


def test_read_tool_rejects_missing_path(tmp_path) -> None:
    tool = ReadTool(root=tmp_path)

    with pytest.raises(ToolError, match="does not exist"):
        tool.run(ToolInput(arguments={"path": "missing.txt"}))


@pytest.mark.parametrize("field", ["start_line", "max_lines"])
def test_read_tool_rejects_invalid_line_arguments(tmp_path, field: str) -> None:
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    tool = ReadTool(root=tmp_path)

    with pytest.raises(ToolError, match="positive integer"):
        tool.run(ToolInput(arguments={"path": "notes.txt", field: 0}))
