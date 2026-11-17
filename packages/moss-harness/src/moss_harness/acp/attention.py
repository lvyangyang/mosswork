"""Read-only SudoCode request facts; these never grant permission to answer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from moss_harness.acp.runtime_status import ConnectionObservation, EngineFact


class AgentIdentity(EngineFact):
    agent_id: str
    name: str
    description: str
    subagent_type: str | None
    model: str | None
    color: str | None
    background: bool


class AttentionRequest(EngineFact):
    id: Annotated[int, Field(ge=0)]
    reason: Literal["permission", "question"]
    phase: Literal["queued", "presented"]
    agent: AgentIdentity | None
    summary: Annotated[str, Field(max_length=240)]


class AttentionSnapshot(EngineFact):
    revision: Annotated[int, Field(ge=0)]
    requests: tuple[AttentionRequest, ...]

    @model_validator(mode="before")
    @classmethod
    def wire_array(cls, value: object) -> object:
        # A JSON array is copied to an immutable projection, not an input queue.
        if isinstance(value, dict) and isinstance(value.get("requests"), list):
            return {**value, "requests": tuple(value["requests"])}
        return value

    @model_validator(mode="after")
    def unique_requests(self) -> Self:
        if len({request.id for request in self.requests}) != len(self.requests):
            raise ValueError("Duplicate attention request ID")
        if sum(request.phase == "presented" for request in self.requests) > 1:
            raise ValueError("More than one request owns input")
        return self


class AttentionObservation(ConnectionObservation):
    snapshot: AttentionSnapshot | None = None


class OrderedAttentionProjection:
    """Connection-local notification ordering; a disconnect invalidates it."""

    def __init__(self) -> None:
        self.snapshot: AttentionSnapshot | None = None
        self._delivery_lock = asyncio.Lock()

    def observe(self, payload: object) -> AttentionSnapshot:
        candidate = AttentionSnapshot.model_validate(payload)
        current = self.snapshot
        if current is not None:
            if candidate.revision < current.revision:
                raise ValueError("Attention revision moved backwards")
            if candidate.revision == current.revision and candidate != current:
                raise ValueError("Attention changed without a revision")
        self.snapshot = candidate
        return candidate

    def invalidate(self) -> None:
        self.snapshot = None

    async def deliver_current(self, revision: int, sink: Callable[[AttentionSnapshot], Awaitable[None]]) -> None:
        """A slow UI sink cannot publish an old revision after a newer one."""
        async with self._delivery_lock:
            current = self.snapshot
            if current is not None and current.revision == revision:
                await sink(current)
