from __future__ import annotations

import json

from tiny_claw._internal.session import SessionManager, SessionMemoryStore


def test_session_manager_resolves_stable_cli_default_session(tmp_path) -> None:
    manager = SessionManager(state_dir=tmp_path / "state", workdir=tmp_path)

    first = manager.resolve_cli(None)
    second = manager.resolve_cli(None)

    assert first == second
    assert first.source == "cli"
    assert first.external_id == "default"
    assert first.workdir == tmp_path.resolve()
    assert first.key.startswith("cli-")


def test_session_manager_resolves_named_cli_sessions_separately(tmp_path) -> None:
    manager = SessionManager(state_dir=tmp_path / "state", workdir=tmp_path)

    default = manager.resolve_cli(None)
    named = manager.resolve_cli("debug-login")

    assert default.key != named.key
    assert named.external_id == "debug-login"
    assert named.display_name == "debug-login"


def test_session_manager_resolves_feishu_chat_session(tmp_path) -> None:
    manager = SessionManager(state_dir=tmp_path / "state", workdir=tmp_path)

    session = manager.resolve_feishu_chat("chat-id")

    assert session.source == "feishu"
    assert session.external_id == "chat:chat-id"
    assert session.display_name == "chat:chat-id"
    assert session.key.startswith("feishu-")


def test_session_manager_writes_metadata(tmp_path) -> None:
    manager = SessionManager(state_dir=tmp_path / "state", workdir=tmp_path)

    session = manager.resolve_cli("release")

    meta_path = tmp_path / "state" / "sessions" / session.key / "meta.json"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["key"] == session.key
    assert payload["source"] == "cli"
    assert payload["external_id"] == "release"
    assert payload["workdir"] == str(tmp_path.resolve())
    assert payload["created_at"]
    assert payload["updated_at"]


def test_session_memory_store_isolates_sessions(tmp_path) -> None:
    manager = SessionManager(state_dir=tmp_path / "state", workdir=tmp_path)
    store = SessionMemoryStore(tmp_path / "state")
    first = manager.resolve_cli("first")
    second = manager.resolve_cli("second")

    store.for_session(first).append("last_prompt", "hello first")
    store.for_session(second).append("last_prompt", "hello second")

    assert store.for_session(first).read_recent(limit=1) == ("last_prompt: hello first",)
    assert store.for_session(second).read_recent(limit=1) == ("last_prompt: hello second",)
