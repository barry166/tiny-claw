"""Middleware hooks for tool calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from tiny_claw._internal.tools.base import ToolOutput


class ToolMiddleware(Protocol):
    def before_call(self, name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        """Return the payload that should be passed to the tool."""

    def after_call(
        self,
        name: str,
        payload: Mapping[str, object],
        result: ToolOutput,
    ) -> ToolOutput:
        """Return the result that should be exposed to the caller."""


@dataclass
class MiddlewareStack:
    middlewares: list[ToolMiddleware] = field(default_factory=list)

    def prepare(self, name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        prepared = payload
        for middleware in self.middlewares:
            prepared = middleware.before_call(name, prepared)
        return prepared

    def finalize(
        self,
        name: str,
        payload: Mapping[str, object],
        result: ToolOutput,
    ) -> ToolOutput:
        finalized = result
        for middleware in reversed(self.middlewares):
            finalized = middleware.after_call(name, payload, finalized)
        return finalized
