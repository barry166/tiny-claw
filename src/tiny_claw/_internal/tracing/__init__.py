"""Lightweight local tracing for agent runs."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

TraceMode = Literal["off", "metadata", "replay"]
TraceStatus = Literal["ok", "error"]

SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)


@dataclass
class TraceSpan:
    span_id: str
    parent_id: str | None
    kind: str
    name: str
    started_at: str
    sequence: int
    ended_at: str | None = None
    status: TraceStatus = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    children: list[TraceSpan] = field(default_factory=list)


@dataclass
class TraceTree:
    schema_version: int
    trace_id: str
    session_key: str
    session_source: str
    capture_mode: TraceMode
    root: TraceSpan


@dataclass(frozen=True)
class TraceRecordInfo:
    trace_id: str
    path: Path | None


class TraceRecorder(Protocol):
    def record(self, tree: TraceTree) -> Path | None:
        """Persist a completed trace tree."""


@dataclass(frozen=True)
class NullTraceRecorder:
    def record(self, tree: TraceTree) -> Path | None:
        return None


@dataclass(frozen=True)
class FileTraceRecorder:
    state_dir: Path

    def record(self, tree: TraceTree) -> Path | None:
        path = self.state_dir / "sessions" / tree.session_key / "traces" / f"{tree.trace_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(trace_tree_to_json(tree), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return path


@dataclass
class _TraceState:
    tree: TraceTree
    spans_by_id: dict[str, TraceSpan]
    recorder: TraceRecorder
    lock: Lock = field(default_factory=Lock)
    sequence: int = 0
    closed: bool = False

    def next_sequence(self) -> int:
        with self.lock:
            self.sequence += 1
            return self.sequence


_TRACE_STATE: ContextVar[_TraceState | None] = ContextVar(
    "tiny_claw_trace_state",
    default=None,
)
_CURRENT_SPAN_ID: ContextVar[str | None] = ContextVar(
    "tiny_claw_current_span_id",
    default=None,
)


@dataclass(frozen=True)
class SpanHandle:
    tracer: Tracer
    state: _TraceState | None
    span_id: str | None

    @property
    def active(self) -> bool:
        return self.state is not None and self.span_id is not None

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        self.tracer.set_span_attributes(self, attributes)

    def record_event(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        self.tracer.record_event(name, attributes or {}, span=self)

    def set_status(self, status: TraceStatus) -> None:
        self.tracer.set_span_status(self, status)


@dataclass(frozen=True)
class Tracer:
    recorder: TraceRecorder = field(default_factory=NullTraceRecorder)
    capture_mode: TraceMode = "metadata"
    max_payload_chars: int = 4_000

    @property
    def enabled(self) -> bool:
        return self.capture_mode != "off"

    def has_active_trace(self) -> bool:
        return _TRACE_STATE.get() is not None

    def current_state(self) -> _TraceState | None:
        return _TRACE_STATE.get()

    def current_span_id(self) -> str | None:
        return _CURRENT_SPAN_ID.get()

    def root_span_id(self) -> str | None:
        state = _TRACE_STATE.get()
        if state is None:
            return None
        return state.tree.root.span_id

    def begin_trace(
        self,
        *,
        trace_id: str,
        session_key: str,
        session_source: str,
        kind: str,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> SpanHandle:
        if not self.enabled:
            return SpanHandle(self, None, None)

        root = TraceSpan(
            span_id=_new_span_id(),
            parent_id=None,
            kind=kind,
            name=name,
            started_at=_now_iso(),
            sequence=0,
            attributes=_safe_attributes(attributes or {}),
        )
        tree = TraceTree(
            schema_version=1,
            trace_id=trace_id,
            session_key=session_key,
            session_source=session_source,
            capture_mode=self.capture_mode,
            root=root,
        )
        state = _TraceState(
            tree=tree,
            spans_by_id={root.span_id: root},
            recorder=self.recorder,
        )
        _TRACE_STATE.set(state)
        _CURRENT_SPAN_ID.set(root.span_id)
        return SpanHandle(self, state, root.span_id)

    @contextmanager
    def start_trace(
        self,
        *,
        trace_id: str,
        session_key: str,
        session_source: str,
        kind: str,
        name: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[SpanHandle]:
        handle = self.begin_trace(
            trace_id=trace_id,
            session_key=session_key,
            session_source=session_source,
            kind=kind,
            name=name,
            attributes=attributes,
        )
        try:
            yield handle
        except Exception as exc:
            handle.set_attributes({"error_type": type(exc).__name__})
            self.end_trace(status="error")
            raise
        else:
            self.end_trace(status="ok")

    def begin_span(
        self,
        *,
        kind: str,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        parent_span_id: str | None = None,
        state: _TraceState | None = None,
    ) -> SpanHandle:
        resolved_state = state or _TRACE_STATE.get()
        if not self.enabled or resolved_state is None or resolved_state.closed:
            return SpanHandle(self, None, None)

        parent_id = parent_span_id or _CURRENT_SPAN_ID.get() or resolved_state.tree.root.span_id
        parent = resolved_state.spans_by_id.get(parent_id)
        if parent is None:
            parent = resolved_state.tree.root
            parent_id = parent.span_id

        span = TraceSpan(
            span_id=_new_span_id(),
            parent_id=parent_id,
            kind=kind,
            name=name,
            started_at=_now_iso(),
            sequence=resolved_state.next_sequence(),
            attributes=_safe_attributes(attributes or {}),
        )
        with resolved_state.lock:
            parent.children.append(span)
            resolved_state.spans_by_id[span.span_id] = span
        _TRACE_STATE.set(resolved_state)
        _CURRENT_SPAN_ID.set(span.span_id)
        return SpanHandle(self, resolved_state, span.span_id)

    @contextmanager
    def start_span(
        self,
        *,
        kind: str,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        parent_span_id: str | None = None,
        state: _TraceState | None = None,
    ) -> Iterator[SpanHandle]:
        previous_state = _TRACE_STATE.get()
        previous_span_id = _CURRENT_SPAN_ID.get()
        handle = self.begin_span(
            kind=kind,
            name=name,
            attributes=attributes,
            parent_span_id=parent_span_id,
            state=state,
        )
        try:
            yield handle
        except Exception as exc:
            handle.set_attributes({"error_type": type(exc).__name__})
            self.end_span(handle, status="error")
            raise
        else:
            self.end_span(handle, status=handle_span_status(handle))
        finally:
            if handle.state is not None and handle.state.closed:
                _TRACE_STATE.set(None)
                _CURRENT_SPAN_ID.set(None)
            else:
                _TRACE_STATE.set(previous_state)
                _CURRENT_SPAN_ID.set(previous_span_id)

    def end_span(
        self,
        handle: SpanHandle,
        *,
        status: TraceStatus = "ok",
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if handle.state is None or handle.span_id is None:
            return
        span = handle.state.spans_by_id.get(handle.span_id)
        if span is None or span.ended_at is not None:
            return
        if attributes:
            span.attributes.update(_safe_attributes(attributes))
        span.status = status
        span.ended_at = _now_iso()
        if _CURRENT_SPAN_ID.get() == span.span_id:
            _CURRENT_SPAN_ID.set(span.parent_id)

    def end_current_span_if_kind(self, kind: str) -> None:
        state = _TRACE_STATE.get()
        current_span_id = _CURRENT_SPAN_ID.get()
        if state is None or current_span_id is None:
            return
        span = state.spans_by_id.get(current_span_id)
        if span is None or span.kind != kind:
            return
        self.end_span(SpanHandle(self, state, current_span_id), status=span.status)

    def end_trace(
        self,
        *,
        status: TraceStatus = "ok",
        attributes: Mapping[str, Any] | None = None,
    ) -> TraceRecordInfo | None:
        state = _TRACE_STATE.get()
        if state is None or state.closed:
            return None
        state.closed = True
        root = state.tree.root
        if attributes:
            root.attributes.update(_safe_attributes(attributes))
        self._close_open_spans(state, status=status)
        try:
            path = state.recorder.record(state.tree)
        except Exception as exc:  # pragma: no cover - recorder implementations vary.
            logger.warning("trace recorder failed: %s", exc)
            path = None
        finally:
            _CURRENT_SPAN_ID.set(None)
            _TRACE_STATE.set(None)
        return TraceRecordInfo(trace_id=state.tree.trace_id, path=path)

    def set_span_attributes(self, handle: SpanHandle, attributes: Mapping[str, Any]) -> None:
        if handle.state is None or handle.span_id is None:
            return
        span = handle.state.spans_by_id.get(handle.span_id)
        if span is None:
            return
        span.attributes.update(_safe_attributes(attributes))

    def set_span_status(self, handle: SpanHandle, status: TraceStatus) -> None:
        if handle.state is None or handle.span_id is None:
            return
        span = handle.state.spans_by_id.get(handle.span_id)
        if span is not None:
            span.status = status

    def record_event(
        self,
        name: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        span: SpanHandle | None = None,
    ) -> None:
        state = span.state if span is not None else _TRACE_STATE.get()
        span_id = span.span_id if span is not None else _CURRENT_SPAN_ID.get()
        if state is None or span_id is None:
            return
        target = state.spans_by_id.get(span_id)
        if target is None:
            return
        target.events.append(
            {
                "timestamp": _now_iso(),
                "name": name,
                "attributes": _safe_attributes(attributes or {}),
            }
        )

    def payload_attributes(self, prefix: str, payload: Any) -> dict[str, Any]:
        try:
            serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        except TypeError:
            serialized = str(payload)

        if self.capture_mode == "replay":
            return {
                prefix: _sanitize_payload(payload, max_chars=self.max_payload_chars),
                f"{prefix}_hash": _sha256(serialized),
                f"{prefix}_chars": len(serialized),
            }
        keys: tuple[str, ...] = ()
        if isinstance(payload, Mapping):
            keys = tuple(sorted(str(key) for key in payload))
        return {
            f"{prefix}_hash": _sha256(serialized),
            f"{prefix}_keys": keys,
            f"{prefix}_chars": len(serialized),
        }

    def _close_open_spans(self, state: _TraceState, *, status: TraceStatus) -> None:
        open_spans = sorted(
            (
                span
                for span in state.spans_by_id.values()
                if span.ended_at is None and span is not state.tree.root
            ),
            key=lambda span: span.sequence,
            reverse=True,
        )
        for span in open_spans:
            if status == "error":
                span.status = "error"
            span.ended_at = _now_iso()
        root = state.tree.root
        if root.ended_at is None:
            root.status = status
            root.ended_at = _now_iso()


class NullTracer(Tracer):
    def __init__(self) -> None:
        super().__init__(recorder=NullTraceRecorder(), capture_mode="off")


def handle_span_status(handle: SpanHandle) -> TraceStatus:
    if handle.state is None or handle.span_id is None:
        return "ok"
    span = handle.state.spans_by_id.get(handle.span_id)
    if span is None:
        return "ok"
    return span.status


def trace_tree_to_json(tree: TraceTree) -> dict[str, Any]:
    return {
        "schema_version": tree.schema_version,
        "trace_id": tree.trace_id,
        "session": {
            "key": tree.session_key,
            "source": tree.session_source,
        },
        "capture_mode": tree.capture_mode,
        "root": span_to_json(tree.root),
    }


def span_to_json(span: TraceSpan) -> dict[str, Any]:
    return {
        "span_id": span.span_id,
        "parent_id": span.parent_id,
        "kind": span.kind,
        "name": span.name,
        "started_at": span.started_at,
        "ended_at": span.ended_at,
        "status": span.status,
        "attributes": span.attributes,
        "events": span.events,
        "children": [span_to_json(child) for child in sorted(span.children, key=_span_sort_key)],
    }


def hash_payload(payload: Any) -> str:
    try:
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        serialized = str(payload)
    return _sha256(serialized)


def elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _safe_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _sanitize_payload(value, max_chars=4_000)
        for key, value in attributes.items()
        if value is not None
    }


def _sanitize_payload(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_payload(raw_value, max_chars=max_chars)
        return sanitized
    if isinstance(value, tuple):
        return [_sanitize_payload(item, max_chars=max_chars) for item in value]
    if isinstance(value, list):
        return [_sanitize_payload(item, max_chars=max_chars) for item in value]
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        marker = f"...[truncated {len(value)} chars]..."
        keep = max(0, max_chars - len(marker))
        return value[:keep] + marker
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _sanitize_payload(str(value), max_chars=max_chars)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _span_sort_key(span: TraceSpan) -> tuple[int, int, int]:
    tool_index = span.attributes.get("tool_call_index")
    if span.kind == "tool.call" and isinstance(tool_index, int):
        return (1, tool_index, span.sequence)
    return (0, span.sequence, span.sequence)


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "FileTraceRecorder",
    "NullTraceRecorder",
    "NullTracer",
    "SpanHandle",
    "TraceMode",
    "TraceRecordInfo",
    "TraceRecorder",
    "TraceSpan",
    "TraceStatus",
    "TraceTree",
    "Tracer",
    "elapsed_ms",
    "hash_payload",
    "span_to_json",
    "trace_tree_to_json",
]
