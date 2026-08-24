"""Uniform empty-with-reason envelope used by every query-layer function.

Mirrors `SignalBatch`: a result with no items must say why. Callers (MCP
tools today, REST issue #59 later) must not invent a silent `[]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class QueryResult(Generic[T]):
    items: tuple[T, ...] = field(default_factory=tuple)
    empty_reason: str | None = None

    def __post_init__(self) -> None:
        if self.items and self.empty_reason is not None:
            raise ValueError("empty_reason is only set when items is empty")
        if not self.items and not self.empty_reason:
            raise ValueError("an empty QueryResult must carry empty_reason -- never a silent gap")

    def to_dict(self) -> dict:
        serialized = []
        for item in self.items:
            if hasattr(item, "model_dump"):
                serialized.append(item.model_dump(mode="json"))
            elif hasattr(item, "__dict__") and not isinstance(item, dict):
                serialized.append({k: v for k, v in item.__dict__.items() if not k.startswith("_")})
            else:
                serialized.append(item)
        return {"items": serialized, "empty_reason": self.empty_reason}
