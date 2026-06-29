"""Memory-test toolkit: markers, polling, and teardown archival.

Tests get ``MemoryProbe`` from the ``memory`` fixture (see conftest). New
memory tests should reuse it rather than hand-rolling memory REST calls.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from band_rest import AsyncRestClient


class MemoryProbe:
    """Memory-test helper: make markers, poll for stored memories, auto-archive on teardown.

    Get it from the ``memory`` fixture. Typical use::

        marker = memory.marker("BADGE")              # always make markers this way
        ...trigger the agent...
        matches = await memory.wait(marker, scope="subject", subject_id=user_id)

    Subject scope REQUIRES ``subject_id`` (else the list API returns nothing); ``wait()``
    raises if it's missing so you can't silently time out on that mistake.
    """

    def __init__(self, client: AsyncRestClient, *, default_timeout: float) -> None:
        self._client = client
        self._default_timeout = default_timeout
        self._ids: list[str] = []

    def marker(self, prefix: str) -> str:
        """Return a unique marker the LLM keeps verbatim (prefix + timestamp + random).

        Opaque tokens get dropped when the model rewrites a fact, so weave the result in as
        the fact's substance (e.g. ``f"badge number is {marker}"``).
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{prefix}-{ts}-{uuid4().hex[:8]}"

    async def wait(
        self,
        marker: str,
        *,
        scope: str,
        subject_id: str | None = None,
        timeout: float | None = None,
    ) -> list[Any]:
        """Poll active ``scope`` memories until one's content contains ``marker``.

        Returns the matches and tracks their ids for teardown archival. Raises
        ``ValueError`` for subject scope without ``subject_id`` (the list API returns
        nothing without it); ``pytest.fail`` on timeout. ``timeout`` defaults to the
        configured E2E timeout.
        """
        if scope == "subject" and subject_id is None:
            raise ValueError('scope="subject" requires subject_id')

        kwargs: dict[str, Any] = {"page_size": 50, "status": "active", "scope": scope}
        if subject_id is not None:
            kwargs["subject_id"] = subject_id

        deadline = asyncio.get_running_loop().time() + (
            timeout or self._default_timeout
        )
        while asyncio.get_running_loop().time() < deadline:
            response = await self._client.agent_api_memories.list_agent_memories(
                **kwargs
            )
            matches = [
                memory
                for memory in response.data or []
                if marker in (getattr(memory, "content", None) or "")
            ]
            if matches:
                self._ids.extend(m.id for m in matches)
                return matches
            await asyncio.sleep(1)

        pytest.fail(f"Expected {scope} memory containing {marker}")

    async def archive_all(self) -> None:
        """Archive every memory matched via ``wait`` (best effort). Called on teardown."""
        for memory_id in self._ids:
            with contextlib.suppress(Exception):
                await self._client.agent_api_memories.archive_agent_memory(id=memory_id)
