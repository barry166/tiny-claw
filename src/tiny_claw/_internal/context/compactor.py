"""Runtime context compaction for provider requests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from tiny_claw._internal.schema.message import Message, Role


@dataclass(frozen=True)
class CompactionResult:
    messages: tuple[Message, ...]
    original_chars: int
    compacted_chars: int
    max_chars: int
    masked_tool_results: int = 0
    truncated_tool_results: int = 0

    @property
    def changed(self) -> bool:
        return self.masked_tool_results > 0 or self.truncated_tool_results > 0

    @property
    def still_over_budget(self) -> bool:
        return self.compacted_chars > self.max_chars


@dataclass(frozen=True)
class ContextCompactor:
    max_chars: int = 120_000
    retain_last_messages: int = 8
    old_tool_result_mask_chars: int = 240
    recent_tool_result_head_chars: int = 2_000
    recent_tool_result_tail_chars: int = 2_000

    def compact(self, messages: Sequence[Message]) -> CompactionResult:
        original = tuple(messages)
        original_chars = _messages_chars(original)
        if original_chars <= self.max_chars:
            return CompactionResult(
                messages=original,
                original_chars=original_chars,
                compacted_chars=original_chars,
                max_chars=self.max_chars,
            )

        protect_start = max(0, len(original) - max(0, self.retain_last_messages))
        last_user_index = _last_user_index(original)
        compacted: list[Message] = []
        masked = 0
        truncated = 0

        for index, message in enumerate(original):
            if not _can_compact_tool_result(message, index=index, last_user_index=last_user_index):
                compacted.append(message)
                continue

            if index < protect_start:
                replacement = _mask_tool_result(message)
                if replacement != message.content:
                    masked += 1
                compacted.append(replace(message, content=replacement))
                continue

            replacement = _head_tail_tool_result(
                message.content,
                head_chars=max(0, self.recent_tool_result_head_chars),
                tail_chars=max(0, self.recent_tool_result_tail_chars),
            )
            if replacement != message.content:
                truncated += 1
            compacted.append(replace(message, content=replacement))

        compacted_messages = tuple(compacted)
        return CompactionResult(
            messages=compacted_messages,
            original_chars=original_chars,
            compacted_chars=_messages_chars(compacted_messages),
            max_chars=self.max_chars,
            masked_tool_results=masked,
            truncated_tool_results=truncated,
        )


def _messages_chars(messages: Sequence[Message]) -> int:
    return sum(len(message.content) for message in messages)


def _last_user_index(messages: Sequence[Message]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is Role.USER:
            return index
    return None


def _can_compact_tool_result(
    message: Message,
    *,
    index: int,
    last_user_index: int | None,
) -> bool:
    if message.role is not Role.TOOL:
        return False
    return last_user_index is None or index != last_user_index


def _mask_tool_result(message: Message) -> str:
    tool_name = message.name or "unknown"
    return (
        "[早期工具输出已清理以节省上下文。"
        f"工具名: {tool_name}。原始长度: {len(message.content)} chars。]"
    )


def _head_tail_tool_result(content: str, *, head_chars: int, tail_chars: int) -> str:
    if not content:
        return content

    marker = f"\n\n...[中间内容已截断，原始长度 {len(content)} chars]...\n\n"
    if len(content) <= head_chars + tail_chars + len(marker):
        return content
    head = content[:head_chars] if head_chars else ""
    tail = content[-tail_chars:] if tail_chars else ""
    return f"{head}{marker}{tail}"
