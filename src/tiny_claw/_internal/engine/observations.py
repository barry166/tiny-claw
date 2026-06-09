"""Observation handling rules shared by normal and resumed runs."""

from __future__ import annotations

from tiny_claw._internal.schema.message import Message


def append_tool_observations(
    messages: list[Message],
    observations: tuple[Message, ...],
) -> bool:
    messages.extend(observations)
    warning = doom_loop_warning_for(observations)
    if warning is not None:
        messages.append(Message.user(warning))
    return any(message.metadata.get("is_error") is True for message in observations)


def approval_required_text(observations: tuple[Message, ...]) -> str:
    if observations:
        return observations[-1].content
    return "工具调用需要人工审批。"


def doom_loop_warning_for(observations: tuple[Message, ...]) -> str | None:
    for observation in observations:
        if observation.metadata.get("doom_loop_detected") is not True:
            continue
        attempt = int(observation.metadata.get("attempt", 0))
        tool_name = str(observation.metadata.get("doom_loop_tool") or observation.name or "unknown")
        return render_doom_loop_warning(attempt=attempt, tool_name=tool_name)
    return None


def render_doom_loop_warning(*, attempt: int, tool_name: str) -> str:
    return (
        f"你似乎陷入了死循环。你刚刚连续 {attempt} 次使用相同的参数调用了 "
        f"'{tool_name}' 工具，并且都失败了。请立即停止这种无效的重试！"
        "你的注意力被当前的报错过度吸引了。你需要："
        "1. 停止猜测参数。跳出当前的局部思维。"
        "2. 彻底改变你的策略。"
        "3. 如果你确实无法通过系统工具解决当前问题，请直接结束任务并向用户说明"
        "你需要什么人工帮助，而不是继续盲目消耗 API 资源尝试。"
    )
