from __future__ import annotations

import json

import pytest

from tiny_claw._internal.tracing import (
    FileTraceRecorder,
    NullTraceRecorder,
    Tracer,
    span_to_json,
)


def test_tracer_records_parent_child_spans_to_file(tmp_path) -> None:
    tracer = Tracer(recorder=FileTraceRecorder(tmp_path), capture_mode="metadata")

    with tracer.start_trace(
        trace_id="trace-1",
        session_key="session-1",
        session_source="test",
        kind="agent.run",
        name="tiny_claw.run",
    ) as root:
        with tracer.start_span(kind="agent.step", name="step.1") as step:
            step.set_attributes({"step": 1})
            step.record_event("context.compacted", {"original_chars": 100})
        root.set_attributes({"stop_reason": "final"})

    payload = json.loads(
        (tmp_path / "sessions" / "session-1" / "traces" / "trace-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["trace_id"] == "trace-1"
    assert payload["session"] == {"key": "session-1", "source": "test"}
    assert payload["root"]["kind"] == "agent.run"
    assert payload["root"]["attributes"]["stop_reason"] == "final"
    child = payload["root"]["children"][0]
    assert child["kind"] == "agent.step"
    assert child["parent_id"] == payload["root"]["span_id"]
    assert child["events"][0]["name"] == "context.compacted"


def test_tracer_marks_span_error_and_closes_trace_on_exception() -> None:
    tracer = Tracer(recorder=NullTraceRecorder(), capture_mode="metadata")

    with (
        pytest.raises(RuntimeError, match="boom"),
        tracer.start_trace(
            trace_id="trace-1",
            session_key="session-1",
            session_source="test",
            kind="agent.run",
            name="tiny_claw.run",
        ) as root,
        tracer.start_span(kind="llm.call", name="llm.fake"),
    ):
        raise RuntimeError("boom")

    tree = root.state.tree if root.state is not None else None
    assert tree is not None
    payload = span_to_json(tree.root)
    assert payload["status"] == "error"
    assert payload["children"][0]["status"] == "error"
    assert payload["children"][0]["attributes"]["error_type"] == "RuntimeError"


def test_end_trace_marks_open_child_spans_error_when_trace_errors() -> None:
    tracer = Tracer(recorder=NullTraceRecorder(), capture_mode="metadata")
    root = tracer.begin_trace(
        trace_id="trace-1",
        session_key="session-1",
        session_source="test",
        kind="agent.run",
        name="tiny_claw.run",
    )
    tracer.begin_span(kind="agent.step", name="step.1")

    tracer.end_trace(status="error")

    tree = root.state.tree if root.state is not None else None
    assert tree is not None
    payload = span_to_json(tree.root)
    assert payload["status"] == "error"
    assert payload["children"][0]["status"] == "error"


def test_metadata_payload_attributes_do_not_store_raw_payload() -> None:
    tracer = Tracer(recorder=NullTraceRecorder(), capture_mode="metadata")

    attributes = tracer.payload_attributes(
        "tool_arguments",
        {"path": "secret.txt", "api_key": "sk-secret"},
    )

    assert "tool_arguments" not in attributes
    assert attributes["tool_arguments_keys"] == ("api_key", "path")
    serialized = json.dumps(attributes)
    assert "sk-secret" not in serialized
    assert "secret.txt" not in serialized
    assert attributes["tool_arguments_hash"]


def test_replay_payload_attributes_redact_and_truncate_raw_payload() -> None:
    tracer = Tracer(
        recorder=NullTraceRecorder(),
        capture_mode="replay",
        max_payload_chars=10,
    )

    attributes = tracer.payload_attributes(
        "tool_arguments",
        {
            "path": "abcdefghijklmno",
            "token": "secret-token",
        },
    )

    assert attributes["tool_arguments"]["token"] == "[redacted]"
    assert attributes["tool_arguments"]["path"].endswith("...[truncated 15 chars]...")
    assert attributes["tool_arguments_hash"]
