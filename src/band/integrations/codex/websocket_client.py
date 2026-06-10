"""Async JSON-RPC client for Codex app-server over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .rpc_base import BaseJsonRpcClient, OverloadRetryPolicy

logger = logging.getLogger(__name__)


class CodexWebSocketClient(BaseJsonRpcClient):
    """Bidirectional JSON-RPC client with a single WebSocket read loop."""

    def __init__(
        self,
        *,
        ws_url: str = "ws://127.0.0.1:8765",
        retry_policy: OverloadRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        connect_timeout_s: float = 10.0,
    ) -> None:
        super().__init__(retry_policy=retry_policy, sleep=sleep)
        self._transport_label = "websocket payload"
        self.ws_url = ws_url
        self._connect_timeout_s = connect_timeout_s

        self._ws: Any = None
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        """Open WebSocket connection to codex app-server."""
        if self._connected:
            return

        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise RuntimeError(
                "websockets package is required for CodexWebSocketClient"
            ) from exc

        # Codex app-server WS does not support permessage-deflate.
        self._ws = await connect(
            self.ws_url,
            compression=None,
            max_size=16 * 1024 * 1024,  # Codex can emit large JSON-RPC payloads.
            open_timeout=self._connect_timeout_s,
        )
        self._connected = True
        self._reader_task = asyncio.create_task(self._read_ws_loop())

    async def close(self) -> None:
        """Close read loop and websocket connection."""
        if not self._connected:
            return
        self._connected = False

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("Exception during ws.close()", exc_info=True)
            self._ws = None

        self._fail_pending("Codex websocket client closed")

    async def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocket not connected")
        await self._ws.send(json.dumps(payload, separators=(",", ":")))

    # ------------------------------------------------------------------
    # WebSocket-specific read loop
    # ------------------------------------------------------------------

    async def _read_ws_loop(self) -> None:
        if self._ws is None:
            return

        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    text = raw.decode("utf-8", errors="replace")
                else:
                    text = str(raw)
                await self._dispatch_rpc_message(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex websocket read loop failed")
        finally:
            if self._connected:
                self._fail_pending("Codex websocket disconnected")
