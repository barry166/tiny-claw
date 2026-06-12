from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from tiny_claw._internal.errors import ProviderError
from tiny_claw._internal.provider.base import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
)
from tiny_claw._internal.provider.tracking import (
    FileUsageRecorder,
    ModelCallScope,
    UsageEvent,
    UsageTrackingProvider,
    clear_run_summary,
    model_call_scope,
    run_summary,
)
from tiny_claw._internal.schema.message import Message, ToolCall
from tiny_claw._internal.tracing import FileTraceRecorder, Tracer


class RecordingProvider:
    def __init__(self, response: LLMResponse | None = None) -> None:
        self.requests: list[LLMRequest] = []
        self.response = response or LLMResponse(
            message=Message.assistant("tracked"),
            provider="inner",
            model="inner-model",
            metadata={"usage": {"prompt_tokens": 2}},
            usage=LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        )

    @property
    def name(self) -> str:
        return "inner"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.response


class FailingProvider:
    @property
    def name(self) -> str:
        return "failing"

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise ProviderError("boom")


@dataclass
class RecordingUsageRecorder:
    events: list[UsageEvent]

    def record(self, event: UsageEvent) -> None:
        self.events.append(event)


class FailingRecorder:
    def record(self, event: UsageEvent) -> None:
        raise RuntimeError("recorder failed")


@pytest.fixture(autouse=True)
def _clear_run_summary() -> None:
    clear_run_summary("run-1")
    yield
    clear_run_summary("run-1")


def test_llm_request_has_no_tracking_context() -> None:
    request = LLMRequest(messages=(Message.user("hello"),))

    assert not hasattr(request, "context")


def test_usage_tracking_provider_records_success_event() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "hello.txt"})
    response = LLMResponse(
        message=Message.assistant(content="tracked", tool_calls=(call,)),
        provider="inner",
        model="inner-model",
        metadata={"usage": {"prompt_tokens": 2}},
        usage=LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5),
    )
    inner = RecordingProvider(response=response)
    recorder = RecordingUsageRecorder(events=[])
    provider = UsageTrackingProvider(inner=inner, recorder=recorder)
    request = LLMRequest(messages=(Message.user("hello"),))

    with model_call_scope(_scope()):
        result = provider.complete(request)

    assert result is response
    assert inner.requests[0] == request
    assert not hasattr(inner.requests[0], "context")
    event = recorder.events[0]
    assert event.session_key == "session-1"
    assert event.session_source == "cli"
    assert event.run_id == "run-1"
    assert event.step == 2
    assert event.provider == "inner"
    assert event.model == "inner-model"
    assert event.usage == LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5)
    assert event.raw_usage == {"prompt_tokens": 2}
    assert event.tool_calls == 1
    assert event.text_chars == len("tracked")
    assert event.latency_ms >= 0
    assert event.error_type is None
    summary = run_summary("run-1")
    assert summary is not None
    assert summary.session_key == "session-1"
    assert summary.session_display_name == "Session One"
    assert summary.input_tokens == 2
    assert summary.output_tokens == 3
    assert summary.unpriced_models == ("inner-model",)


def test_usage_tracking_provider_records_without_scope() -> None:
    recorder = RecordingUsageRecorder(events=[])
    provider = UsageTrackingProvider(inner=RecordingProvider(), recorder=recorder)

    provider.complete(LLMRequest(messages=(Message.user("hello"),)))

    event = recorder.events[0]
    assert event.session_key is None
    assert event.session_source is None
    assert event.run_id is None
    assert event.step is None
    assert event.caller is None


def test_usage_tracking_provider_records_failure_and_reraises() -> None:
    recorder = RecordingUsageRecorder(events=[])
    provider = UsageTrackingProvider(inner=FailingProvider(), recorder=recorder)

    with pytest.raises(ProviderError, match="boom"), model_call_scope(_scope()):
        provider.complete(LLMRequest(messages=(Message.user("hello"),)))

    event = recorder.events[0]
    assert event.provider == "failing"
    assert event.session_key == "session-1"
    assert event.error_type == "ProviderError"
    assert event.usage is None
    assert event.tool_calls is None
    summary = run_summary("run-1")
    assert summary is not None
    assert summary.input_tokens == 0
    assert summary.output_tokens == 0


def test_usage_tracking_provider_records_error_span(tmp_path) -> None:
    tracer = Tracer(recorder=FileTraceRecorder(tmp_path), capture_mode="metadata")
    provider = UsageTrackingProvider(
        inner=FailingProvider(),
        recorder=RecordingUsageRecorder(events=[]),
        tracer=tracer,
    )

    with (
        pytest.raises(ProviderError, match="boom"),
        tracer.start_trace(
            trace_id="trace-1",
            session_key="session-1",
            session_source="test",
            kind="agent.run",
            name="tiny_claw.run",
        ),
    ):
        provider.complete(LLMRequest(messages=(Message.user("hello"),)))

    payload = json.loads(
        (tmp_path / "sessions" / "session-1" / "traces" / "trace-1.json").read_text(
            encoding="utf-8"
        )
    )
    span = payload["root"]["children"][0]
    assert span["kind"] == "llm.call"
    assert span["status"] == "error"
    assert span["attributes"]["error_type"] == "ProviderError"


def test_usage_tracking_provider_ignores_recorder_failure() -> None:
    provider = UsageTrackingProvider(inner=RecordingProvider(), recorder=FailingRecorder())

    with model_call_scope(_scope()):
        result = provider.complete(LLMRequest(messages=(Message.user("hello"),)))

    assert result.text == "tracked"


def test_file_usage_recorder_writes_jsonl_without_message_content(tmp_path) -> None:
    recorder = FileUsageRecorder(tmp_path)

    recorder.record(
        UsageEvent(
            timestamp="2026-06-11T00:00:00+00:00",
            session_key="session-1",
            session_source="cli",
            run_id="run-1",
            mode="act",
            phase="act",
            step=1,
            max_steps=2,
            caller="main_loop",
            provider="openai",
            model="gpt-test",
            usage=LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5),
            raw_usage={"prompt_tokens": 2},
            latency_ms=12,
            tool_calls=0,
            text_chars=6,
        )
    )

    payload = json.loads((tmp_path / "usage" / "model-calls.jsonl").read_text())
    assert payload["session_key"] == "session-1"
    assert payload["usage"]["total_tokens"] == 5
    serialized = json.dumps(payload)
    assert "hello prompt" not in serialized
    assert "assistant response" not in serialized
    assert "tool arguments" not in serialized


def _scope() -> ModelCallScope:
    return ModelCallScope(
        session_key="session-1",
        session_source="cli",
        session_display_name="Session One",
        run_id="run-1",
        mode="act",
        phase="act",
        step=2,
        max_steps=3,
        caller="main_loop",
    )
