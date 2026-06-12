"""Provider decorator for model usage tracking."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from tiny_claw._internal.provider.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

logger = logging.getLogger(__name__)
_WRITE_LOCK = Lock()

_CNY_PER_MILLION_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5.4": (Decimal("1.25"), Decimal("10.00")),
    "claude-sonnet-4-20250514": (Decimal("21.00"), Decimal("105.00")),
}


@dataclass(frozen=True)
class ModelCallScope:
    session_key: str
    session_source: str
    session_display_name: str
    run_id: str
    mode: str
    phase: str | None
    step: int
    max_steps: int
    caller: str


@dataclass(frozen=True)
class UsageRunSummary:
    run_id: str
    session_key: str | None
    session_display_name: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_cny: Decimal = Decimal("0")
    unpriced_models: tuple[str, ...] = field(default_factory=tuple)


_MODEL_CALL_SCOPE: ContextVar[ModelCallScope | None] = ContextVar(
    "tiny_claw_model_call_scope",
    default=None,
)
_RUN_SUMMARIES: dict[str, UsageRunSummary] = {}
_RUN_SUMMARIES_LOCK = Lock()


@dataclass(frozen=True)
class UsageEvent:
    timestamp: str
    session_key: str | None
    session_source: str | None
    run_id: str | None
    mode: str | None
    phase: str | None
    step: int | None
    max_steps: int | None
    caller: str | None
    provider: str
    model: str | None
    usage: LLMUsage | None
    raw_usage: Mapping[str, Any] | None
    latency_ms: int
    tool_calls: int | None
    text_chars: int | None
    error_type: str | None = None


class UsageRecorder(Protocol):
    def record(self, event: UsageEvent) -> None:
        """Record one model usage event."""


@dataclass(frozen=True)
class NullUsageRecorder:
    def record(self, event: UsageEvent) -> None:
        pass


@dataclass(frozen=True)
class FileUsageRecorder:
    state_dir: Path

    @property
    def path(self) -> Path:
        return self.state_dir / "usage" / "model-calls.jsonl"

    def record(self, event: UsageEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_event_payload(event), ensure_ascii=True) + "\n")


@dataclass(frozen=True)
class UsageTrackingProvider:
    inner: LLMProvider
    recorder: UsageRecorder

    @property
    def name(self) -> str:
        return self.inner.name

    def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = self.inner.complete(request)
        except Exception as exc:
            self._record_event(
                request=request,
                response=None,
                latency_ms=_elapsed_ms(started),
                error_type=type(exc).__name__,
            )
            raise

        self._record_event(
            request=request,
            response=response,
            latency_ms=_elapsed_ms(started),
            error_type=None,
        )
        return response

    def _record_event(
        self,
        *,
        request: LLMRequest,
        response: LLMResponse | None,
        latency_ms: int,
        error_type: str | None,
    ) -> None:
        event = _usage_event(
            provider=self.inner.name,
            request=request,
            response=response,
            latency_ms=latency_ms,
            error_type=error_type,
        )
        _update_run_summary(event)
        try:
            self.recorder.record(event)
        except Exception as exc:  # pragma: no cover - recorder implementations vary.
            logger.warning("usage recorder failed: %s", exc)


def _usage_event(
    *,
    provider: str,
    request: LLMRequest,
    response: LLMResponse | None,
    latency_ms: int,
    error_type: str | None,
) -> UsageEvent:
    scope = _MODEL_CALL_SCOPE.get()
    raw_usage = _raw_usage(response)
    return UsageEvent(
        timestamp=datetime.now(UTC).isoformat(),
        session_key=_scope_value(scope, "session_key"),
        session_source=_scope_value(scope, "session_source"),
        run_id=_scope_value(scope, "run_id"),
        mode=_scope_value(scope, "mode"),
        phase=_scope_value(scope, "phase"),
        step=_scope_value(scope, "step"),
        max_steps=_scope_value(scope, "max_steps"),
        caller=_scope_value(scope, "caller"),
        provider=response.provider if response is not None else provider,
        model=response.model if response is not None else None,
        usage=response.usage if response is not None else None,
        raw_usage=raw_usage,
        latency_ms=latency_ms,
        tool_calls=len(response.message.tool_calls) if response is not None else None,
        text_chars=len(response.text) if response is not None else None,
        error_type=error_type,
    )


@contextmanager
def model_call_scope(scope: ModelCallScope) -> Iterator[None]:
    token = _MODEL_CALL_SCOPE.set(scope)
    try:
        yield
    finally:
        _MODEL_CALL_SCOPE.reset(token)


def run_summary(run_id: str) -> UsageRunSummary | None:
    with _RUN_SUMMARIES_LOCK:
        return _RUN_SUMMARIES.get(run_id)


def clear_run_summary(run_id: str) -> None:
    with _RUN_SUMMARIES_LOCK:
        _RUN_SUMMARIES.pop(run_id, None)


def log_run_summary(logger: logging.Logger, *, run_id: str) -> None:
    summary = run_summary(run_id)
    if summary is None:
        return
    session_id = summary.session_display_name or summary.session_key or "unknown"
    logger.info("会话 ID: %s", session_id)
    logger.info("总消耗 Input Tokens: %s", summary.input_tokens)
    logger.info("总消耗 Output Tokens: %s", summary.output_tokens)
    logger.info("总计费用 (CNY): ¥%s", _format_cny(summary.total_cost_cny))
    logger.info("==========================================")


def _scope_value(scope: ModelCallScope | None, name: str) -> Any:
    if scope is None:
        return None
    return getattr(scope, name)


def _raw_usage(response: LLMResponse | None) -> Mapping[str, Any] | None:
    if response is None or response.metadata is None:
        return None
    usage = response.metadata.get("usage")
    if isinstance(usage, Mapping):
        return usage
    return None


def _event_payload(event: UsageEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload["usage"] = asdict(event.usage) if event.usage is not None else None
    return payload


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _update_run_summary(event: UsageEvent) -> None:
    if event.run_id is None:
        return
    input_tokens = event.usage.input_tokens if event.usage is not None else 0
    output_tokens = event.usage.output_tokens if event.usage is not None else 0
    input_tokens = input_tokens or 0
    output_tokens = output_tokens or 0
    cost = _cost_cny(event.model, input_tokens=input_tokens, output_tokens=output_tokens)
    unpriced_models: tuple[str, ...] = ()
    if cost is None:
        cost = Decimal("0")
        if event.model:
            unpriced_models = (event.model,)

    with _RUN_SUMMARIES_LOCK:
        current = _RUN_SUMMARIES.get(event.run_id)
        if current is None:
            current = UsageRunSummary(
                run_id=event.run_id,
                session_key=event.session_key,
                session_display_name=_scope_value(_MODEL_CALL_SCOPE.get(), "session_display_name"),
            )
        merged_unpriced = tuple(sorted(set(current.unpriced_models).union(unpriced_models)))
        _RUN_SUMMARIES[event.run_id] = UsageRunSummary(
            run_id=current.run_id,
            session_key=current.session_key or event.session_key,
            session_display_name=current.session_display_name
            or _scope_value(_MODEL_CALL_SCOPE.get(), "session_display_name"),
            input_tokens=current.input_tokens + input_tokens,
            output_tokens=current.output_tokens + output_tokens,
            total_cost_cny=current.total_cost_cny + cost,
            unpriced_models=merged_unpriced,
        )


def _cost_cny(model: str | None, *, input_tokens: int, output_tokens: int) -> Decimal | None:
    if model is None:
        return None
    prices = _CNY_PER_MILLION_TOKENS.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return Decimal(input_tokens) * input_price / Decimal(1_000_000) + Decimal(
        output_tokens
    ) * output_price / Decimal(1_000_000)


def _format_cny(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.000001'))}"
