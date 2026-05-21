"""AgentCore handler — invokes AWS Bedrock AgentCore agent runtimes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from thenvoi.runtime.tools import AgentTools


class AgentCoreClient(Protocol):
    """Minimal interface for the boto3 bedrock-agentcore client."""

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]: ...


logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 1_048_576  # 1 MB
_READ_CHUNK_SIZE = 65_536  # 64 KB


class AgentCoreHandler:
    """Handler that invokes an AWS Bedrock AgentCore agent runtime.

    Each instance is bound to a single agent runtime ARN.
    Create multiple instances for multiple agents.

    **Timeout layering**: This handler applies its own ``timeout`` (default
    120 s) via ``asyncio.wait_for`` around the boto3 invocation.  The bridge
    router may apply an *additional* outer timeout (``BridgeConfig.handler_timeout``,
    default 300 s) around the entire ``handle()`` call.  The inner handler
    timeout fires first; the outer router timeout acts as a safety net.
    """

    def __init__(
        self,
        agent_runtime_arn: str,
        region: str,
        timeout: float = 120.0,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        mcp_tool_name: str = "chat",
        boto3_client: AgentCoreClient | None = None,
    ) -> None:
        if not agent_runtime_arn or not agent_runtime_arn.strip():
            raise ValueError("agent_runtime_arn must be a non-empty string")
        if not region or not region.strip():
            raise ValueError("region must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")

        self._agent_runtime_arn = agent_runtime_arn.strip()
        self._region = region.strip()
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._mcp_tool_name = mcp_tool_name
        self._boto3_client = boto3_client

    def _get_client(self) -> AgentCoreClient:
        """Return the boto3 client, creating it lazily if not injected.

        Called from the asyncio event loop thread (before ``to_thread``),
        so concurrent coroutines are serialised by cooperative scheduling
        and no lock is needed.
        """
        if self._boto3_client is None:
            try:
                import boto3
            except ImportError:
                raise ImportError(
                    "boto3 is required for AgentCoreHandler. "
                    "Install with: pip install band-sdk[bridge_agentcore]"
                )
            self._boto3_client = boto3.client(
                "bedrock-agentcore", region_name=self._region
            )
        return self._boto3_client

    def _build_payload(
        self,
        content: str,
        thread_id: str,
    ) -> dict[str, Any]:
        """Build an MCP JSON-RPC ``tools/call`` payload for AgentCore.

        AgentCore runtimes expose MCP tool servers. The handler sends a
        ``tools/call`` request targeting ``mcp_tool_name`` (default
        ``chat``) with the message content as the ``message`` argument.
        """
        return {
            "jsonrpc": "2.0",
            "id": thread_id,
            "method": "tools/call",
            "params": {
                "name": self._mcp_tool_name,
                "arguments": {"message": content},
            },
        }

    def _read_streaming_response(self, response: dict[str, Any]) -> str:
        """Read and parse the streaming response from AgentCore.

        Dispatches to focused parsers in order:
        1. SSE unwrapping
        2. MCP JSON-RPC extraction
        3. Generic JSON key lookup
        4. Plain text fallback
        """
        text = self._read_body(response)
        json_text = self._unwrap_sse(text)

        try:
            data = json.loads(json_text)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Response is not JSON, using raw text")
            return text

        if isinstance(data, dict):
            mcp_result = self._parse_mcp_response(data)
            if mcp_result is not None:
                return mcp_result

            json_result = self._extract_known_json_key(data)
            if json_result is not None:
                return json_result

        return text

    def _read_body(self, response: dict[str, Any]) -> str:
        """Read the streaming body, enforcing the size limit incrementally."""
        body = response.get("response") or response.get("body")
        if body is None:
            raise RuntimeError("Response missing 'response' (StreamingBody)")

        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = body.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                if total + len(chunk) > self._max_response_bytes:
                    raise RuntimeError(
                        f"Response exceeds {self._max_response_bytes} byte limit"
                    )
                total += len(chunk)
                chunks.append(chunk)
            raw = b"".join(chunks)
            return raw.decode("utf-8")
        finally:
            body.close()

    @staticmethod
    def _unwrap_sse(text: str) -> str:
        """Strip SSE ``event: ...\\ndata: ...`` wrapper if present."""
        if not text.startswith("event:"):
            return text
        for line in text.splitlines():
            if line.startswith("data:"):
                return line[len("data:") :].strip()
        return text

    @staticmethod
    def _parse_mcp_response(data: dict[str, Any]) -> str | None:
        """Extract text from an MCP JSON-RPC result or error."""
        result = data.get("result")
        if isinstance(result, dict):
            content_list = result.get("content")
            if isinstance(content_list, list):
                texts = [
                    c["text"]
                    for c in content_list
                    if isinstance(c, dict) and c.get("type") == "text"
                ]
                if texts:
                    return "\n".join(texts)

        error = data.get("error")
        if isinstance(error, dict):
            return f"AgentCore error: {error.get('message', str(error))}"

        return None

    @staticmethod
    def _extract_known_json_key(data: dict[str, Any]) -> str | None:
        """Return the first value matching a known response key."""
        for key in ("output", "response", "text", "content", "message"):
            if key in data:
                value = data[key]
                return str(value) if not isinstance(value, str) else value
        return None

    async def _invoke_agent(
        self,
        payload: dict[str, str],
        session_id: str,
    ) -> str:
        """Invoke the AgentCore runtime in a thread (boto3 is sync).

        Both the API call and the response body read happen inside the
        thread so that ``StreamingBody.read()`` never blocks the event loop.
        """
        client = self._get_client()

        def _call() -> str:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=self._agent_runtime_arn,
                runtimeSessionId=session_id,
                contentType="application/json",
                accept="application/json, text/event-stream",
                payload=json.dumps(payload).encode("utf-8"),
            )
            return self._read_streaming_response(response)

        return await asyncio.to_thread(_call)

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
        """Handle a routed @mention message by invoking AgentCore."""
        # Send a thought event (non-fatal if it fails)
        try:
            await tools.send_event(
                content=f"Invoking AgentCore for @{mentioned_agent}...",
                message_type="thought",
            )
        except Exception as exc:
            logger.warning("Failed to send thought event: %s", exc)

        payload = self._build_payload(
            content=content,
            thread_id=thread_id,
        )

        try:
            response_text = await asyncio.wait_for(
                self._invoke_agent(payload, session_id=thread_id),
                timeout=self._timeout,
            )
        except TimeoutError:
            raise TimeoutError(
                f"AgentCore invocation timed out after {self._timeout}s "
                f"for @{mentioned_agent}"
            ) from None

        if not response_text or not response_text.strip():
            raise RuntimeError(
                f"AgentCore returned empty response for @{mentioned_agent}"
            )

        # Prefer handle for mention resolution (most reliable for the
        # platform), fall back to sender_name.  If neither is available,
        # send without mentions rather than mentioning a raw UUID.
        mention_identifier = sender_handle or sender_name
        kwargs: dict[str, Any] = {"content": response_text}
        if mention_identifier:
            kwargs["mentions"] = [mention_identifier]
        await tools.send_message(**kwargs)
