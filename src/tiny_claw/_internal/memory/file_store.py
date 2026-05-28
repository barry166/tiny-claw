"""File-system backed memory store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileMemoryStore:
    root: Path

    @property
    def path(self) -> Path:
        return self.root / "memory.jsonl"

    def append(self, key: str, value: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "value": value}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def read_recent(self, *, limit: int) -> tuple[str, ...]:
        if not self.path.exists():
            return ()

        lines = self.path.read_text(encoding="utf-8").splitlines()
        entries: list[str] = []
        for line in lines[-limit:]:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(payload, dict):
                continue

            key = str(payload.get("key", ""))
            value = str(payload.get("value", ""))
            if key or value:
                entries.append(f"{key}: {value}")
        return tuple(entries)
