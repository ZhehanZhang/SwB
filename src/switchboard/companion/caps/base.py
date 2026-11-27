from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CapabilityError(ValueError):
    """An error whose message can be sent to the deployment."""


@dataclass(frozen=True)
class ActionSpec:
    description: str
    schema: dict


class JobContext(Protocol):
    job_id: str

    async def progress(self, data: dict) -> None: ...

    async def claim(self, kind: str, payload: dict) -> dict: ...


class Capability(Protocol):
    id: str
    version: str
    title: str
    actions: dict[str, ActionSpec]

    def available(self) -> str | None: ...

    async def invoke(self, action: str, args: dict, ctx: JobContext) -> dict: ...
