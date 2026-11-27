from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class CapabilityError(ValueError):
    pass


@dataclass(frozen=True)
class ActionSpec:
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class JobContext:
    job_id: str
    _progress: Callable[[dict[str, Any]], Awaitable[None]] = field(repr=False)
    _claim: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] = field(repr=False)

    async def progress(self, data: dict[str, Any]) -> None:
        await self._progress(data)

    async def claim(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._claim(kind, payload)


@dataclass(frozen=True)
class CompanionContext:
    companion_id: str
    _emit: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] = field(repr=False)

    async def emit(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._emit(kind, payload)


class Capability:
    id: str
    version: str
    title: str
    actions: dict[str, ActionSpec]

    def available(self) -> str | None:
        return None

    async def invoke(self, action: str, args: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        raise NotImplementedError

    async def run(self, ctx: CompanionContext) -> None:
        pass

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "actions": [
                {"name": name, "description": spec.description, "schema": spec.schema}
                for name, spec in self.actions.items()
            ],
        }
