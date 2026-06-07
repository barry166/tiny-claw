"""Readable log rendering for engine runs."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tiny_claw._internal.schema.message import ToolCall
from tiny_claw._internal.tools.base import ToolOutput

ARG_PREVIEW_CHARS = 800
TEXT_PREVIEW_CHARS = 1200
RETURN_PREVIEW_CHARS = 500

COLOR_RESET = "\033[0m"
COLOR_DIM = "\033[2m"
COLOR_BLUE = "\033[34m"
COLOR_CYAN = "\033[36m"
COLOR_GREEN = "\033[32m"
COLOR_MAGENTA = "\033[35m"
COLOR_RED = "\033[31m"
COLOR_YELLOW = "\033[33m"


def log_run_start(
    logger: logging.Logger,
    *,
    provider: str,
    mode: str,
    max_steps: int,
    workdir: Path,
    session_key: str,
    session_source: str,
    registered_tools: int,
    memories: int,
) -> None:
    logger.info(
        (
            "%s provider=%s mode=%s max_steps=%s tools=%s memories=%s "
            "session=%s source=%s\n%s workdir=%s"
        ),
        color("[Engine] 主循环开始", COLOR_CYAN),
        provider,
        mode,
        max_steps,
        registered_tools,
        memories,
        session_key,
        session_source,
        color("[WorkDir]", COLOR_DIM),
        workdir,
    )


def log_turn_start(logger: logging.Logger, *, step: int, max_steps: int, phase: str) -> None:
    logger.info(
        "%s",
        color(f"========== [Turn {step}/{max_steps}] {phase} ==========", COLOR_MAGENTA),
    )


def log_model_request(
    logger: logging.Logger,
    *,
    messages: int,
    tool_choice: str,
    visible_tools: int,
) -> None:
    logger.info(
        "%s messages=%s tool_choice=%s visible_tools=%s",
        color("[Engine] 发起模型请求", COLOR_BLUE),
        messages,
        tool_choice,
        visible_tools,
    )


def log_context_compaction(
    logger: logging.Logger,
    *,
    original_chars: int,
    compacted_chars: int,
    max_chars: int,
    masked_tool_results: int,
    truncated_tool_results: int,
    still_over_budget: bool,
) -> None:
    level = logger.warning if still_over_budget else logger.info
    level(
        (
            "%s original_chars=%s compacted_chars=%s max_chars=%s "
            "masked_tool_results=%s truncated_tool_results=%s over_budget=%s"
        ),
        color("[Context] 上下文已压缩", COLOR_YELLOW),
        original_chars,
        compacted_chars,
        max_chars,
        masked_tool_results,
        truncated_tool_results,
        still_over_budget,
    )


def log_model_response(
    logger: logging.Logger,
    *,
    provider: str,
    tool_calls: tuple[ToolCall, ...],
    text: str,
) -> None:
    logger.info(
        "%s provider=%s tool_calls=%s text_chars=%s",
        color("[Engine] 收到模型响应", COLOR_CYAN),
        provider,
        len(tool_calls),
        len(text),
    )
    if text:
        logger.info(
            "%s\n%s",
            color("[Assistant] 对外回复:", COLOR_CYAN),
            indent(preview_block(text, max_chars=TEXT_PREVIEW_CHARS)),
        )
    if tool_calls:
        logger.info(
            "%s 请求调用 %s 个工具",
            color("[Assistant]", COLOR_YELLOW),
            len(tool_calls),
        )


def log_tool_call(logger: logging.Logger, call: ToolCall) -> None:
    logger.info(
        "  -> %s 执行工具: %s\n%s",
        color("🛠", COLOR_YELLOW),
        color(call.name, COLOR_YELLOW),
        indent(
            "args: "
            + color(
                preview_text(format_json(call.arguments), max_chars=ARG_PREVIEW_CHARS),
                COLOR_DIM,
            ),
            prefix="     ",
        ),
    )


def log_tool_result(logger: logging.Logger, *, name: str, output: ToolOutput) -> None:
    if output.is_error:
        marker = color("❌ 工具失败", COLOR_RED)
        level = logger.warning
    else:
        marker = color("✅ 工具成功", COLOR_GREEN)
        level = logger.info

    level(
        "  -> %s: %s (返回 %s 字符)\n%s",
        marker,
        color(name, COLOR_GREEN if not output.is_error else COLOR_RED),
        len(output.content),
        indent(
            "output:\n"
            + indent(preview_block(output.content, max_chars=TEXT_PREVIEW_CHARS), prefix="  "),
            prefix="     ",
        ),
    )


def log_tool_exception(logger: logging.Logger, *, name: str, error: str) -> None:
    logger.warning(
        "  -> %s: %s\n%s",
        color("❌ 工具异常", COLOR_RED),
        color(name, COLOR_RED),
        indent("error: " + error, prefix="     "),
    )


def log_run_complete(
    logger: logging.Logger,
    *,
    provider: str,
    stop_reason: str,
    steps: int,
    max_steps: int,
    mode: str,
    phase: str | None,
    tool_policy: str,
) -> None:
    phase_text = "" if phase is None else f" phase={phase}"
    logger.info(
        "%s reason=%s steps=%s/%s provider=%s mode=%s%s tool_policy=%s",
        color("[Engine] 主循环结束", COLOR_GREEN),
        stop_reason,
        steps,
        max_steps,
        provider,
        mode,
        phase_text,
        tool_policy,
    )


def log_run_return(
    logger: logging.Logger,
    *,
    text: str,
    provider: str,
    stop_reason: str,
) -> None:
    logger.info(
        "%s provider=%s reason=%s text_chars=%s",
        color("[Run 完成] 主循环返回", COLOR_GREEN),
        provider,
        stop_reason,
        len(text),
    )
    if text:
        logger.debug(
            "%s\n%s",
            color("[Run 完成] final 预览:", COLOR_GREEN),
            indent(
                "final:\n"
                + indent(preview_block(text, max_chars=RETURN_PREVIEW_CHARS), prefix="  "),
                prefix="  ",
            ),
        )


def format_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


def preview_text(text: str, *, max_chars: int) -> str:
    normalized = text.replace("\n", "\\n")
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + f"...<truncated {len(normalized) - max_chars} chars>"


def preview_block(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...<truncated {len(text) - max_chars} chars>"


def indent(text: str, *, prefix: str = "  ") -> str:
    return "\n".join(prefix + line if line else prefix.rstrip() for line in text.splitlines())


def color(text: object, color_code: str) -> str:
    return f"{color_code}{text}{COLOR_RESET}"
