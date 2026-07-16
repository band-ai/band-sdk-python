"""Memory store-side capture and assertions for live E2E tests.

The *store* layer of memory observation: whether a memory record actually landed
in the backend, not merely that ``band_store_memory`` was called (that call-layer
check is ``observations.tool_calls.MemoryToolCalls``). Hence ``assert_stored``
here vs ``assert_store_called`` there.

Memories are **agent-scoped, not room-scoped**, so this reads with the agent's
own credentials (``agent_api_memories.list_agent_memories``) rather than the
room/user view a ``ReplyCapture`` holds. Tests reach it through
``ReplyCapture.memory(agent)``, which builds the agent-auth client from the
``ProvisionedAgent`` and returns a ``MemoryObservation`` -- they never touch a REST
client directly.

Read after the turn settles (after the ``wait_for_processed`` barrier), so the stored row is durable.
``read`` takes server-side filters (subject_id, scope, system, type, segment,
content_query, status); ``where(...)`` adds the same filtering in-memory so a
single read can be sliced several ways. Matching is tolerant: ``content`` is a
case-insensitive substring, and enum dimensions match by string value (so the
core ``band.core.memory_types`` enums and the Fern ``AgentMemory*`` enums compare
equal).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from band_rest import AgentMemory, AsyncRestClient

from tests.e2e.baseline.toolkit.observations.matching import tolerant_match
from tests.e2e.baseline.toolkit.observations.tool_calls import MemoryToolCalls

logger = logging.getLogger(__name__)


class Memories(list[AgentMemory]):
    """An agent's stored memory records: a ``list[AgentMemory]`` with fluent,
    tolerant filtering and assertions.

    Read once (see ``Memories.read`` / ``ReplyCapture.memory``), then ``where(...)``
    to slice and assert as many times as needed.
    """

    @classmethod
    async def read(
        cls,
        client: AsyncRestClient,
        *,
        subject_id: str | None = None,
        scope: Any | None = None,
        system: Any | None = None,
        type: Any | None = None,
        segment: Any | None = None,
        content_query: str | None = None,
        status: Any | None = None,
        limit: int = 100,
    ) -> Memories:
        """Read the agent's accessible memories via the agent-auth client.

        All filters are server-side (the ``list_agent_memories`` query). Omits
        unset filters rather than sending ``null`` (the OMIT-vs-null convention).
        Enum values are coerced to their string form for the wire. ``subject_id``
        is required by the API for a subject-scoped query. ``limit`` caps how many
        records are returned (the first N), named to match the sibling readers
        (``Events.read``/``ToolCalls.read``); it maps to the API's ``page_size``.
        """
        kwargs: dict[str, Any] = {"page_size": limit}
        for key, value in {
            "subject_id": subject_id,
            "scope": scope,
            "system": system,
            "type": type,
            "segment": segment,
            "content_query": content_query,
            "status": status,
        }.items():
            if value is not None:
                kwargs[key] = str(value)
        response = await client.agent_api_memories.list_agent_memories(**kwargs)
        return cls(response.data or [])

    def where(
        self,
        *,
        content: str | None = None,
        scope: Any | None = None,
        system: Any | None = None,
        type: Any | None = None,
        segment: Any | None = None,
        status: Any | None = None,
        subject_id: str | None = None,
        source_agent_id: str | None = None,
    ) -> Memories:
        """Return a re-wrapped ``Memories`` of records matching every given
        dimension (in-memory, tolerant). Chains into the assertions below.

        Accepts more dimensions than ``read``'s server-side filters on purpose:
        ``content`` and ``source_agent_id`` have no ``list_agent_memories`` filter,
        so they are only matchable here, in memory, after the read.
        """
        dims = {
            "content": content,
            "scope": scope,
            "system": system,
            "type": type,
            "segment": segment,
            "status": status,
            "subject_id": subject_id,
            "source_agent_id": source_agent_id,
        }
        wanted = {key: value for key, value in dims.items() if value is not None}
        return Memories(
            memory
            for memory in self
            if all(
                tolerant_match(value, getattr(memory, key, None))
                for key, value in wanted.items()
            )
        )

    def assert_stored(
        self,
        *,
        content: str | None = None,
        scope: Any | None = None,
        system: Any | None = None,
        type: Any | None = None,
        segment: Any | None = None,
        status: Any | None = None,
    ) -> None:
        """Assert at least one stored memory matches every given dimension."""
        dims = {
            "content": content,
            "scope": scope,
            "system": system,
            "type": type,
            "segment": segment,
            "status": status,
        }
        if not self.where(**dims):
            wanted = {key: value for key, value in dims.items() if value is not None}
            observed = [
                {
                    "content": m.content,
                    "scope": str(m.scope),
                    "system": str(m.system),
                    "type": str(m.type),
                    "segment": str(m.segment),
                    "status": str(m.status),
                }
                for m in self
            ] or ["<none>"]
            raise AssertionError(
                f"expected a stored memory matching {wanted}, but none did; "
                f"observed: {observed}"
            )

    def assert_present(self, *, what: str = "a stored memory") -> None:
        """Assert at least one memory was read."""
        if not self:
            raise AssertionError(f"expected {what}, but none were read")

    def assert_none(self) -> None:
        """Assert no memory was read (e.g. after supersede/archive removed it)."""
        if self:
            observed = [m.content for m in self]
            raise AssertionError(
                f"expected no stored memory, but found {len(self)}: {observed}"
            )

    def assert_at_least(self, n: int) -> None:
        """Assert a threshold-of-N stored memories (a floor, never an exact count)."""
        if len(self) < n:
            raise AssertionError(
                f"expected at least {n} stored memory/memories, got {len(self)}"
            )


@dataclass(frozen=True)
class MemoryObservation:
    """Both layers of memory observation from a single ``ReplyCapture.memory`` read.

    The layers come from different sources and credentials, so each stays its own
    collection with its own assertions; this just bundles them under one accessor:

    - ``calls`` (:class:`MemoryToolCalls`) -- the *call* layer: which memory tools
      the agent invoked, with which params. ``calls.assert_store_called(...)``.
    - ``stored`` (:class:`Memories`) -- the *store* layer: which memory records
      actually landed. ``stored.assert_stored(...)`` / ``stored.where(...)``.

    The names keep the altitude explicit: ``assert_store_called`` (invoked) vs
    ``assert_stored`` (a record exists).
    """

    calls: MemoryToolCalls
    stored: Memories
