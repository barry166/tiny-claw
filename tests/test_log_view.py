from __future__ import annotations

import logging

from tiny_claw._internal.engine import log_view
from tiny_claw._internal.schema.message import ToolCall
from tiny_claw._internal.tools.base import ToolOutput


def test_preview_text_truncates_with_remaining_count() -> None:
    preview = log_view.preview_text("abcdef", max_chars=3)

    assert preview == "abc...<truncated 3 chars>"


def test_preview_block_preserves_newlines() -> None:
    preview = log_view.preview_block("one\ntwo", max_chars=20)

    assert preview == "one\ntwo"


def test_format_json_keeps_unicode_and_sorts_keys() -> None:
    formatted = log_view.format_json({"z": 1, "a": "中文"})

    assert formatted == '{"a": "中文", "z": 1}'


def test_log_tool_call_renders_readable_block(caplog) -> None:
    call = ToolCall(id="call-1", name="bash", arguments={"command": "node -v", "cwd": "."})

    with caplog.at_level(logging.INFO):
        log_view.log_tool_call(logging.getLogger("test-log-view"), call)

    assert "执行工具" in caplog.text
    assert "bash" in caplog.text
    assert '"command": "node -v"' in caplog.text


def test_log_tool_result_renders_success_block(caplog) -> None:
    output = ToolOutput(content="stdout:\nHello, tiny-claw!")

    with caplog.at_level(logging.INFO):
        log_view.log_tool_result(logging.getLogger("test-log-view"), name="bash", output=output)

    assert "工具成功" in caplog.text
    assert "返回 25 字符" in caplog.text
    assert "Hello, tiny-claw!" in caplog.text


def test_log_tool_result_renders_error_block(caplog) -> None:
    output = ToolOutput(content="exit_code=1\nstderr:\nmissing", is_error=True)

    with caplog.at_level(logging.WARNING):
        log_view.log_tool_result(logging.getLogger("test-log-view"), name="bash", output=output)

    assert "工具失败" in caplog.text
    assert "exit_code=1" in caplog.text


def test_log_tool_error_fallback_renders_user_visible_hint(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        log_view.log_tool_error_fallback(
            logging.getLogger("test-log-view"),
            name="read",
            error_type="read_path_not_found",
            attempt_count=1,
            retryable=True,
            suggested_tool="bash",
        )

    assert "工具错误兜底已触发" in caplog.text
    assert "已把失败翻译成下一步建议" in caplog.text
    assert "tool=read" in caplog.text
    assert "error_type=read_path_not_found" in caplog.text
    assert "suggested_tool=bash" in caplog.text
