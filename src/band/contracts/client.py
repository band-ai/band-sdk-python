"""Native participant APIs that keep room authority separate from SDK chat receipts."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import TracebackType
from typing import Self
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from band.contracts.errors import ContractIdentityError, ContractProtocolError
from band.contracts.models import (
    ContractConnection,
    ContractIdentity,
    ContractProposal,
    InboxPage,
)
from band.contracts.transport import ContractTransport
from band.contracts.wire import JsonObject, encode_json


class ContractClient:
    """Open authenticated native contract rooms through the Band agent API."""

    def __init__(
        self, connection: ContractConnection, *, http: httpx.AsyncClient | None = None
    ) -> None:
        self._transport = ContractTransport(connection, http=http)

    async def room(self, room: str, session: str, *, actor: str) -> ContractRoom:
        if not room or not session or not actor:
            raise ValueError("Room, session and authenticated actor are required")
        path = f"/api/v1/agent/chats/{quote(room, safe='')}/contracts/{quote(session, safe='')}"
        result = ContractRoom(self._transport, path=path, session=session, actor=actor)
        await result.connect()
        return result

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def gateway(self, *, session: str, actor: str) -> ContractRoom:
        """Connect directly to a configured native room gateway URL."""
        if not session or not actor:
            raise ValueError("Session and authenticated actor are required")
        result = ContractRoom(self._transport, session=session, actor=actor)
        await result.connect()
        return result

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


class ContractRoom:
    """Bind every operation to the authenticated actor and immutable session."""

    def __init__(
        self, transport: ContractTransport, *, path: str = "", session: str, actor: str
    ) -> None:
        self._transport = transport
        self._path = path
        self._session = session
        self._actor = actor
        self._identity: ContractIdentity | None = None
        self._definition: JsonObject | None = None
        self._connect_lock = asyncio.Lock()
        self._definition_lock = asyncio.Lock()

    @property
    def session(self) -> str:
        return self._session

    @property
    def actor(self) -> str:
        return self._actor

    async def connect(self) -> ContractIdentity:
        async with self._connect_lock:
            if self._identity is None:
                body = await self._transport.request("GET", self._path + "/v1/identity")
                try:
                    identity = ContractIdentity.model_validate(body)
                except ValidationError:
                    raise ContractIdentityError(
                        "Authority does not support the required native room profiles"
                    ) from None
                if identity.actor != self.actor or identity.session != self.session:
                    raise ContractIdentityError(
                        "Authority identity differs from the bound actor or session"
                    )
                self._identity = identity
        return self._identity.model_copy(deep=True)

    async def identity(self) -> ContractIdentity:
        return await self.connect()

    async def definition(self) -> JsonObject:
        identity = await self.connect()
        async with self._definition_lock:
            if self._definition is None:
                if self._path:
                    body = await self._transport.request(
                        "POST", self._path + "/v1/view", body=b"{}"
                    )
                    definition = self._native_definition(body, identity)
                else:
                    body = await self._transport.request("GET", "/v1/definition")
                    if (
                        body.get("profile") != "contract-room/definition/1"
                        or body.get("session") != self.session
                        or body.get("actor") != self.actor
                        or body.get("identity") != identity.identity
                        or not isinstance(body.get("definition"), dict)
                    ):
                        raise ContractIdentityError(
                            "Definition differs from the bound room identity"
                        )
                    definition = body["definition"]
                self._definition = deepcopy(definition)
            return deepcopy(self._definition)

    def _native_definition(
        self, body: JsonObject, identity: ContractIdentity
    ) -> JsonObject:
        view = body.get("view")
        checkpoint = body.get("checkpoint")
        if not isinstance(view, dict) or not isinstance(checkpoint, dict):
            raise ContractIdentityError(
                "Native view is missing its projection or checkpoint"
            )
        definition = view.get("definition")
        state = checkpoint.get("state")
        if not isinstance(definition, dict) or not isinstance(state, dict):
            raise ContractIdentityError(
                "Native view is missing its definition or saved state"
            )
        contract = definition.get("contract")
        role = definition.get("role")
        bindings = state.get("bindings")
        if (
            not isinstance(contract, dict)
            or not isinstance(role, dict)
            or not isinstance(bindings, dict)
        ):
            raise ContractIdentityError(
                "Native definition is missing its contract, role, or bindings"
            )
        digest = contract.get("digest")
        source = contract.get("source_digest")
        role_name = role.get("name")
        if (
            body.get("profile") != "band-contract/agent-view/1"
            or body.get("actor") != self.actor
            or view.get("id") != self.session
            or state.get("id") != self.session
            or not isinstance(digest, str)
            or not digest
            or digest != checkpoint.get("definition")
            or digest != identity.identity.get("definition")
            or not isinstance(source, str)
            or not source
            or source != checkpoint.get("source")
            or source != identity.identity.get("source")
            or not isinstance(role_name, str)
            or bindings.get(role_name) != self.actor
        ):
            raise ContractIdentityError(
                "Definition differs from the bound room identity"
            )
        return definition

    async def view(self, *, hydrate: bool = False) -> JsonObject:
        await self.connect()
        result = await self._transport.request(
            "POST", self._path + "/v1/view", body=b"{}"
        )
        view = result.get("view")
        if not isinstance(view, dict):
            raise ContractProtocolError("Room view is missing its projection")
        if hydrate and "definition" not in view:
            view["definition"] = await self.definition()
        return result

    def proposal(
        self, *, id: str, kind: str, payload: JsonObject, expected_version: int
    ) -> ContractProposal:
        return ContractProposal(
            id=id,
            session=self.session,
            kind=kind,
            payload=payload,
            expected_version=expected_version,
        )

    async def propose(self, proposal: ContractProposal) -> JsonObject:
        await self.connect()
        if proposal.session != self.session:
            raise ContractIdentityError("Proposal belongs to a different session")
        result = await self._transport.request(
            "POST", self._path + "/v1/proposals", body=proposal.encoded
        )
        if result.get("status") not in ("accepted", "duplicate", "rejected"):
            raise ContractProtocolError(
                "Proposal response does not establish acceptance or rejection"
            )
        if result.get("status") in ("accepted", "duplicate"):
            receipt = (
                result.get("original") if result["status"] == "duplicate" else result
            )
            if not isinstance(receipt, dict) or (
                receipt.get("status") != "accepted"
                or receipt.get("id") != proposal.id
                or receipt.get("session") != self.session
                or receipt.get("actor") != self.actor
            ):
                raise ContractIdentityError(
                    "Accepted proposal differs from the submitted identity"
                )
        return result

    async def inbox(self, *, after: int, limit: int = 100) -> InboxPage:
        await self.connect()
        if (
            type(after) is not int
            or after < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError(
                "Provide a nonnegative native cursor and a limit from 1 to 100"
            )
        result = await self._transport.request(
            "POST",
            self._path + "/v1/inbox",
            body=encode_json({"after": after, "limit": limit}),
        )
        try:
            page = InboxPage.model_validate(result)
        except ValidationError:
            raise ContractProtocolError(
                "Authority returned an invalid native inbox page"
            ) from None
        if page.cursor < after:
            raise ContractProtocolError(
                "Authority returned a native cursor behind the requested cursor"
            )
        return page
