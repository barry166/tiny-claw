"""Session resolution and session-scoped memory storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tiny_claw._internal.memory.file_store import FileMemoryStore


@dataclass(frozen=True)
class SessionRef:
    key: str
    source: str
    external_id: str
    workdir: Path
    display_name: str


@dataclass(frozen=True)
class SessionManager:
    state_dir: Path
    workdir: Path

    def resolve_cli(self, session_name: str | None = None) -> SessionRef:
        name = _normalize_segment(session_name or "default")
        return self._resolve(
            source="cli",
            external_id=name,
            display_name=name,
        )

    def resolve_feishu_chat(self, chat_id: str) -> SessionRef:
        normalized_chat_id = _normalize_segment(chat_id)
        return self._resolve(
            source="feishu",
            external_id=f"chat:{normalized_chat_id}",
            display_name=f"chat:{chat_id}",
        )

    def _resolve(self, *, source: str, external_id: str, display_name: str) -> SessionRef:
        resolved_workdir = self.workdir.resolve()
        key = _session_key(
            source=source,
            workdir=resolved_workdir,
            external_id=external_id,
        )
        session = SessionRef(
            key=key,
            source=source,
            external_id=external_id,
            workdir=resolved_workdir,
            display_name=display_name,
        )
        self._write_meta(session)
        return session

    def memory_store(self, session: SessionRef) -> FileMemoryStore:
        return SessionMemoryStore(self.state_dir).for_session(session)

    def _write_meta(self, session: SessionRef) -> None:
        path = self.state_dir / "sessions" / session.key / "meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        created_at = now
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            if isinstance(existing, dict):
                created_at = str(existing.get("created_at") or now)
        payload = {
            "key": session.key,
            "source": session.source,
            "external_id": session.external_id,
            "display_name": session.display_name,
            "workdir": str(session.workdir),
            "created_at": created_at,
            "updated_at": now,
        }
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class SessionMemoryStore:
    state_dir: Path

    def for_session(self, session: SessionRef) -> FileMemoryStore:
        return FileMemoryStore(self.state_dir / "sessions" / session.key)


def _session_key(*, source: str, workdir: Path, external_id: str) -> str:
    workdir_hash = hashlib.sha256(str(workdir).encode("utf-8")).hexdigest()[:12]
    return "-".join(
        (
            _normalize_segment(source),
            workdir_hash,
            _normalize_segment(external_id),
        )
    )


def _normalize_segment(value: str) -> str:
    normalized = []
    for char in value.strip().lower():
        if char.isalnum() or char in {"-", "_"}:
            normalized.append(char)
        else:
            normalized.append("-")
    result = "".join(normalized).strip("-")
    return result or "default"
