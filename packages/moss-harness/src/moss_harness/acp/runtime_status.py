"""Validated engine facts for a capability-negotiated ACP observation.

The wire contract is SudoCode's documented runtime-status extension v1.
Connection ownership and availability belong to ACPService, independently of
the engine's foreground state and existing background task counters.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RUNTIME_STATUS_BUDGET_SECONDS = 2.0


class EngineFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ActiveForeground(EngineFact):
    state: Literal["idle", "running"]


class WaitingForeground(EngineFact):
    state: Literal["awaiting_input"]
    waiting_for: Literal["permission", "question"]


class BackgroundSummary(EngineFact):
    terminals: Annotated[int, Field(ge=0)]
    agents: Annotated[int, Field(ge=0)]
    execution_unconfirmed: Annotated[int, Field(ge=0)]
    tool_operations: Annotated[int, Field(ge=0)]


class RuntimeSnapshot(EngineFact):
    foreground: Annotated[ActiveForeground | WaitingForeground, Field(discriminator="state")]
    background: BackgroundSummary


class ConnectionObservation(EngineFact):
    scope: Literal["moss-owned-acp-connections"] = "moss-owned-acp-connections"
    is_complete: Literal[False] = False
    instance_id: str | None = None
    session_id: str | None = None
    availability: Literal["fresh", "unsupported", "unavailable", "disconnected"]


class RuntimeObservation(ConnectionObservation):
    snapshot: RuntimeSnapshot | None = None


def supports_runtime_status(metadata: dict[str, Any] | None) -> bool:
    return supports_engine_observation(metadata, "runtimeStatus")


def supports_engine_observation(metadata: dict[str, Any] | None, name: str) -> bool:
    """Accept the advertised version, never infer support from a runner name."""
    if not isinstance(metadata, dict):
        return False
    namespace = metadata.get("sudocode")
    if not isinstance(namespace, dict):
        return False
    capability = namespace.get(name)
    if not isinstance(capability, dict):
        return False
    version = capability.get("version")
    return type(version) is int and version == 1
