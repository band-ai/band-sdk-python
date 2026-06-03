"""HTTP client for the OpenCode server API."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Protocol
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


class OpencodeClientProtocol(Protocol):
    """Interface used by the adapter for OpenCode transport calls."""

    async def create_session(
        self,
        *,
        title: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_session(self, session_id: str) -> dict[str, Any]: ...

    async def prompt_async(
        self,
        session_id: str,
        *,
        parts: list[dict[str, Any]],
        system: str | None = None,
        model: dict[str, str] | None = None,
        agent: str | None = None,
        variant: str | None = None,
    ) -> None: ...

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        *,
        response: str,
    ) -> None: ...

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None: ...

    async def reject_question(self, request_id: str) -> None: ...

    async def abort_session(self, session_id: str) -> None: ...

    async def register_mcp_server(self, *, name: str, url: str) -> dict[str, Any]: ...

    async def deregister_mcp_server(self, name: str) -> None: ...

    def iter_events(self) -> AsyncIterator[dict[str, Any]]: ...

    async def close(self) -> None: ...


class HttpOpencodeClient(OpencodeClientProtocol):
    """Minimal async client for the OpenCode HTTP and SSE API."""

    def __init__(
        self,
        *,
        base_url: str,
        directory: str | None = None,
        workspace: str | None = None,
        timeout_s: float = 300.0,
    ) -> None:
        headers: dict[str, str] = {}
        if directory:
            headers["x-opencode-directory"] = (
                quote(directory)
                if any(ord(ch) > 127 for ch in directory)
                else directory
            )
        if workspace:
            headers["x-opencode-workspace"] = workspace

        self._directory = directory
        self._workspace = workspace
        self._last_event_id: str | None = None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(30.0, read=timeout_s),
        )

    def _query_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._directory:
            params["directory"] = self._directory
        if self._workspace:
            params["workspace"] = self._workspace
        return params

    async def create_session(
        self,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title

        response = await self._client.post(
            "/session",
            params=self._query_params(),
            json=payload or None,
        )
        response.raise_for_status()
        return response.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/session/{session_id}",
            params=self._query_params(),
        )
        response.raise_for_status()
        return response.json()

    async def prompt_async(
        self,
        session_id: str,
        *,
        parts: list[dict[str, Any]],
        system: str | None = None,
        model: dict[str, str] | None = None,
        agent: str | None = None,
        variant: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"parts": parts}
        if system:
            payload["system"] = system
        if model:
            payload["model"] = model
        if agent:
            payload["agent"] = agent
        if variant:
            payload["variant"] = variant

        response = await self._client.post(
            f"/session/{session_id}/prompt_async",
            params=self._query_params(),
            json=payload,
        )
        response.raise_for_status()

    async def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        *,
        response: str,
    ) -> None:
        resp = await self._client.post(
            f"/session/{session_id}/permissions/{permission_id}",
            params=self._query_params(),
            json={"response": response},
        )
        resp.raise_for_status()

    async def reply_question(
        self, request_id: str, *, answers: list[list[str]]
    ) -> None:
        response = await self._client.post(
            f"/question/{request_id}/reply",
            params=self._query_params(),
            json={"answers": answers},
        )
        response.raise_for_status()

    async def reject_question(self, request_id: str) -> None:
        response = await self._client.post(
            f"/question/{request_id}/reject",
            params=self._query_params(),
        )
        response.raise_for_status()

    async def abort_session(self, session_id: str) -> None:
        response = await self._client.post(
            f"/session/{session_id}/abort",
            params=self._query_params(),
        )
        response.raise_for_status()

    async def register_mcp_server(self, *, name: str, url: str) -> dict[str, Any]:
        response = await self._client.post(
            "/mcp",
            params=self._query_params(),
            json={
                "name": name,
                "config": {"type": "remote", "url": url},
            },
        )
        response.raise_for_status()
        return response.json()

    async def deregister_mcp_server(self, name: str) -> None:
        response = await self._client.delete(
            f"/mcp/{name}",
            params=self._query_params(),
        )
        response.raise_for_status()

    async def iter_events(self) -> AsyncIterator[dict[str, Any]]:
        headers: dict[str, str] = {}
        if self._last_event_id:
            headers["Last-Event-ID"] = self._last_event_id

        async with self._client.stream(
            "GET",
            "/event",
            params=self._query_params(),
            headers=headers,
            timeout=httpx.Timeout(None, read=60.0),
        ) as response:
            response.raise_for_status()

            event_name: str | None = None
            event_id: str | None = None
            data_lines: list[str] = []

            async for line in response.aiter_lines():
                if line == "":
                    if data_lines:
                        payload = "\n".join(data_lines)
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            logger.warning(
                                "Skipping malformed OpenCode SSE payload: %s",
                                payload,
                            )
                        else:
                            if (
                                event_name
                                and isinstance(event, dict)
                                and "type" not in event
                            ):
                                event["type"] = event_name
                            if isinstance(event, dict):
                                yield event
                        if event_id is not None:
                            self._last_event_id = event_id
                    event_name = None
                    event_id = None
                    data_lines = []
                    continue

                if line.startswith("event:"):
                    event_name = line[6:].strip() or None
                    continue

                if line.startswith("id:"):
                    event_id = line[3:].strip() or None
                    continue

                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

    async def close(self) -> None:
        await self._client.aclose()
