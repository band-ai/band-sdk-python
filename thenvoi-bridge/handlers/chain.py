"""LangChain handler — invokes LangChain agents via HTTP POST."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from thenvoi.runtime.tools import AgentTools

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0  # seconds
_MAX_RESPONSE_BYTES = 1_048_576  # 1 MB


class LangChainHandler:
    """Handler that invokes a LangChain agent via HTTP POST.

    Each instance maps agent usernames to HTTP endpoints.  Pass a single
    ``base_url`` when all agents share one server, or a ``urls`` mapping
    for per-agent endpoints.

    **URL resolution**: When ``base_url`` is used, ``/invoke`` is appended
    automatically (e.g. ``http://host:8000`` becomes ``http://host:8000/invoke``).
    When ``urls`` is used, each URL is sent as-is — include the full path
    (e.g. ``http://host:8000/invoke``) in each mapping entry.

    **Timeout layering**: This handler applies its own ``timeout`` (default
    120 s) via the httpx client timeout.  The bridge router may apply an
    *additional* outer timeout (``BridgeConfig.handler_timeout``, default
    300 s) around the entire ``handle()`` call.  The inner handler timeout
    fires first; the outer router timeout acts as a safety net.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        urls: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        httpx_client: httpx.AsyncClient | None = None,
    ) -> None:
        if base_url is None and urls is None:
            raise ValueError(
                "Either base_url or urls must be provided; "
                "set base_url for a shared endpoint or urls for per-agent endpoints"
            )
        if base_url is not None and urls is not None:
            raise ValueError("Provide either base_url or urls, not both")
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")

        if base_url is not None:
            base_url = base_url.strip()
            if not base_url:
                raise ValueError("base_url must be a non-empty string")

        if urls is not None:
            if not urls:
                raise ValueError("urls must be a non-empty mapping")
            cleaned: dict[str, str] = {}
            for agent, url in urls.items():
                agent_stripped = agent.strip()
                url_stripped = url.strip()
                if not agent_stripped:
                    raise ValueError("Agent name in urls must be non-empty")
                if not url_stripped:
                    raise ValueError(
                        f"URL for agent '{agent_stripped}' must be non-empty"
                    )
                cleaned[agent_stripped] = url_stripped
            urls = cleaned

        self._base_url = base_url
        self._urls = urls
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._httpx_client = httpx_client
        self._owns_client = httpx_client is None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the httpx async client, creating it lazily if not injected.

        Called from the asyncio event loop thread, so concurrent coroutines
        are serialised by cooperative scheduling and no lock is needed.
        """
        if self._httpx_client is None:
            try:
                import httpx as _httpx
            except ImportError:
                raise ImportError(
                    "httpx is required for LangChainHandler. "
                    "Install with: pip install band-sdk[bridge_langchain]"
                )
            self._httpx_client = _httpx.AsyncClient(
                timeout=_httpx.Timeout(self._timeout)
            )
        return self._httpx_client

    async def close(self) -> None:
        """Close the underlying httpx client if it was created by this handler.

        Injected clients are *not* closed — the caller owns their lifecycle.
        Safe to call multiple times or when no client was ever created.
        """
        if self._httpx_client is not None and self._owns_client:
            await self._httpx_client.aclose()
            self._httpx_client = None

    def _resolve_url(self, mentioned_agent: str) -> str:
        """Resolve the HTTP endpoint URL for the given agent.

        When ``urls`` is set, looks up the agent name.  When ``base_url``
        is set, appends ``/invoke`` as the default LangChain endpoint.

        Raises:
            ValueError: If the agent has no configured URL.
        """
        if self._urls is not None:
            url = self._urls.get(mentioned_agent)
            if url is None:
                raise ValueError(
                    f"No URL configured for agent '{mentioned_agent}'; "
                    f"known agents: {', '.join(sorted(self._urls))}"
                )
            return url
        # base_url is guaranteed non-None by __init__ validation:
        # __init__ requires exactly one of base_url / urls.
        if self._base_url is None:  # pragma: no cover
            raise RuntimeError("base_url is unexpectedly None")
        return f"{self._base_url.rstrip('/')}/invoke"

    def _build_payload(
        self,
        content: str,
        room_id: str,
        thread_id: str,
        message_id: str,
        sender_id: str,
        sender_name: str | None,
        sender_type: str,
    ) -> dict[str, Any]:
        """Build the JSON payload for the LangChain invocation.

        Follows the LangChain ``/invoke`` endpoint contract:
        ``input`` for the prompt, ``config.configurable`` for thread
        routing, and ``metadata`` for Thenvoi context.

        Uses ``thread_id`` (not ``room_id``) for the LangChain
        configurable thread — the bridge defaults thread_id to room_id
        but they are semantically different (thread_id provides session
        continuity within a room).
        """
        payload: dict[str, Any] = {
            "input": content,
            "config": {
                "configurable": {
                    "thread_id": thread_id,
                },
            },
            "metadata": {
                "thenvoi_room_id": room_id,
                "thenvoi_message_id": message_id,
                "thenvoi_sender_id": sender_id,
                "thenvoi_sender_type": sender_type,
            },
        }
        if sender_name is not None:
            payload["metadata"]["thenvoi_sender_name"] = sender_name
        return payload

    def _extract_response(self, data: dict[str, Any]) -> str:
        """Extract the response text from the LangChain JSON response.

        Tries known LangChain response keys in priority order:
        ``output``, ``response``, ``text``, ``content``, ``message``.
        Falls back to the full JSON string if no known key is found.
        """
        for key in ("output", "response", "text", "content", "message"):
            if key in data:
                value = data[key]
                return str(value) if not isinstance(value, str) else value
        # No known key found — return full JSON.  Log a warning so
        # operators can add the correct key to the extraction list.
        logger.warning(
            "No known response key found in LangChain response; "
            "returning full JSON. Keys present: %s",
            list(data.keys()),
        )
        return json.dumps(data)

    async def _invoke_agent(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> str:
        """POST to the LangChain agent and return the response text.

        Uses httpx streaming to enforce the byte-size limit incrementally,
        preventing large responses from being fully buffered in memory.

        Raises:
            httpx.TimeoutException: If the request exceeds the timeout.
            httpx.HTTPStatusError: If the response has a non-2xx status.
            RuntimeError: If the response body is empty or exceeds the size limit.
        """
        client = self._get_client()

        async with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes(chunk_size=65_536):
                total += len(chunk)
                if total > self._max_response_bytes:
                    raise RuntimeError(
                        f"Response exceeds {self._max_response_bytes} byte limit"
                    )
                chunks.append(chunk)

        content = b"".join(chunks)
        if not content:
            raise RuntimeError("LangChain agent returned empty response body")

        text = content.decode("utf-8", errors="replace")

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Response is not JSON — use raw text
            stripped = text.strip()
            if not stripped:
                raise RuntimeError("LangChain agent returned empty response")
            return stripped

        if isinstance(data, dict):
            return self._extract_response(data)

        # Non-dict JSON (e.g. a string or list)
        return str(data)

    async def handle(
        self,
        content: str,
        room_id: str,
        thread_id: str,
        message_id: str,
        sender_id: str,
        sender_name: str | None,
        sender_handle: str | None,
        sender_type: str,
        mentioned_agent: str,
        tools: AgentTools,
    ) -> None:
        """Handle a routed @mention message by invoking a LangChain agent."""
        # Send a thought event (non-fatal if it fails)
        try:
            await tools.send_event(
                content=f"Invoking LangChain agent for @{mentioned_agent}...",
                message_type="thought",
            )
        except Exception as exc:
            logger.warning("Failed to send thought event: %s", exc)

        url = self._resolve_url(mentioned_agent)
        payload = self._build_payload(
            content=content,
            room_id=room_id,
            thread_id=thread_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_type=sender_type,
        )

        # Import httpx before the try block so the except handler can
        # reference it without an inline import.  _get_client() would
        # import it anyway; doing it here keeps the error path clean.
        try:
            import httpx as _httpx
        except ImportError:
            raise ImportError(
                "httpx is required for LangChainHandler. "
                "Install with: pip install band-sdk[bridge_langchain]"
            )

        try:
            response_text = await self._invoke_agent(url, payload)
        except (_httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise TimeoutError(
                f"LangChain invocation timed out after {self._timeout}s "
                f"for @{mentioned_agent}"
            ) from exc
        except _httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"LangChain agent returned HTTP {exc.response.status_code} "
                f"for @{mentioned_agent}: {exc.response.reason_phrase}"
            ) from exc

        # Prefer handle for mention resolution (most reliable for the
        # platform), fall back to sender_name.
        mention_identifier = sender_handle or sender_name
        kwargs: dict[str, Any] = {"content": response_text}
        if mention_identifier:
            kwargs["mentions"] = [mention_identifier]
        await tools.send_message(**kwargs)

    @classmethod
    def from_env(cls, env_value: str, **kwargs: Any) -> LangChainHandler:
        """Create a handler from the ``LANGCHAIN_URLS`` env var format.

        Supported formats:

        * Single URL: ``http://localhost:8000/invoke``
        * Per-agent:  ``alice:http://localhost:8000,bob:http://localhost:8001``

        Additional keyword arguments are forwarded to the constructor.
        """
        env_value = env_value.strip()
        if not env_value:
            raise ValueError("LANGCHAIN_URLS must be non-empty")

        # If the value contains a colon-separated agent:url pair, parse as urls dict
        # Heuristic: if there's a comma or the first segment before ":" looks like
        # an agent name (not a URL scheme like "http" or "https"), use urls mode.
        parts = [p.strip() for p in env_value.split(",") if p.strip()]

        # Check if this looks like per-agent mapping (agent:url pairs).
        # Only http:// and https:// are recognised as URL schemes; other
        # schemes (e.g. ftp://) would be misinterpreted as agent names.
        first_part = parts[0]
        colon_idx = first_part.find(":")
        if colon_idx > 0:
            prefix = first_part[:colon_idx].lower()
            if prefix not in ("http", "https"):
                # Per-agent URL mapping
                urls: dict[str, str] = {}
                for part in parts:
                    pieces = part.split(":", maxsplit=1)
                    if len(pieces) != 2 or not pieces[0].strip():
                        raise ValueError(
                            f"Invalid LANGCHAIN_URLS entry '{part}'; "
                            f"expected 'agent_name:url' format"
                        )
                    agent = pieces[0].strip()
                    url = pieces[1].strip()
                    if not agent or not url:
                        raise ValueError(
                            f"Invalid LANGCHAIN_URLS entry '{part}'; "
                            f"agent name and URL must be non-empty"
                        )
                    urls[agent] = url
                return cls(urls=urls, **kwargs)

        # Single URL (no agent mapping)
        if len(parts) != 1:
            raise ValueError(
                "LANGCHAIN_URLS with multiple entries must use "
                "'agent:url' format, e.g. 'alice:http://host:8000,bob:http://host:8001'"
            )
        return cls(base_url=parts[0], **kwargs)
