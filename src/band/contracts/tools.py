"""Portable contract tools for existing SDK adapters' additional_tools parameter."""

from __future__ import annotations

from pydantic import Field

from band.contracts.client import ContractRoom
from band.contracts.errors import ContractProposalRejected
from band.contracts.models import FrozenModel, Identifier, Nonnegative
from band.contracts.wire import JsonObject
from band.runtime.custom_tools import CustomToolDef, declares_turn_effect
from band.runtime.tools.types import TurnEffect


class ContractViewInput(FrozenModel):
    """Read the current authenticated contract projection."""


class ContractDefinitionInput(FrozenModel):
    """Read the immutable definition for this actor and session."""


class ContractInboxInput(FrozenModel):
    """Read native receipts after a host-managed cursor without acknowledging chat."""

    after: Nonnegative
    limit: int = Field(default=100, ge=1, le=100)


class ContractProposeInput(FrozenModel):
    """Submit one stable proposal; exact retries must keep the same ID and body."""

    id: Identifier
    kind: Identifier
    payload: JsonObject
    expected_version: Nonnegative


class BoundContractTools:
    def __init__(self, room: ContractRoom) -> None:
        self.room = room

    async def view(self, request: ContractViewInput) -> JsonObject:
        return await self.room.view()

    async def definition(self, request: ContractDefinitionInput) -> JsonObject:
        return await self.room.definition()

    async def inbox(self, request: ContractInboxInput) -> JsonObject:
        return (
            await self.room.inbox(after=request.after, limit=request.limit)
        ).model_dump(mode="json", exclude_none=True)

    @declares_turn_effect(TurnEffect.ACT)
    async def propose(self, request: ContractProposeInput) -> JsonObject:
        result = await self.room.propose(
            self.room.proposal(
                id=request.id,
                kind=request.kind,
                payload=request.payload,
                expected_version=request.expected_version,
            )
        )
        if result["status"] not in ("accepted", "duplicate"):
            raise ContractProposalRejected(
                "The native contract authority rejected this proposal"
            )
        return result


def contract_tools(room: ContractRoom) -> list[CustomToolDef]:
    """Bind participant tools to one authenticated room for any existing adapter."""
    tools = BoundContractTools(room)
    return [
        (ContractViewInput, tools.view),
        (ContractDefinitionInput, tools.definition),
        (ContractInboxInput, tools.inbox),
        (ContractProposeInput, tools.propose),
    ]
