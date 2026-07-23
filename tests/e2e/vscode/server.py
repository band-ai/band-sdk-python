"""Run band-mcp (SSE) as a restartable subprocess for the suite.

The server holds the provisioned Copilot agent identity: VS Code's MCP client
presents no credentials, so the agent key travels in the subprocess env. It is
restartable on a stable port because the workspace's ``mcp.json`` is written
once — the L3 cell restarts this process to prove the surface survives its
platform bridge going away and coming back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket

logger = logging.getLogger(__name__)

# Without this allowlist band-mcp's DNS-rebinding protection answers 421 to
# loopback SSE clients (same value the copilot_docker examples use).
ALLOWED_HOSTS = '["localhost:*","127.0.0.1:*"]'

# The memory tool group is opt-in and is the suite's recall path: band-mcp
# exposes no room-history tool, so cross-session recall flows through
# band_store_memory / band_list_memories.
TOOL_GROUPS = "memory"

READY_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 10.0


def _reserve_port() -> int:
    """Take one ephemeral loopback port from the OS, then release it for the server.

    The close→rebind gap is the standard reservation race — acceptable for a
    single local server (mirrors ``parlant_server._reserve_two_ports``).
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BandMCPServer:
    """Lifecycle of one band-mcp SSE subprocess bound to a stable loopback port."""

    def __init__(
        self, command: list[str], *, agent_key: str, base_url: str, port: int = 0
    ) -> None:
        """``port=0`` reserves an ephemeral one; a fixed port keeps the workspace's
        mcp.json stable across runs, so VS Code's remembered MCP-server trust
        holds and reruns need no re-approval."""
        self._command = command
        self._agent_key = agent_key
        self._base_url = base_url
        self._port = port or _reserve_port()
        self._process: asyncio.subprocess.Process | None = None

    @property
    def sse_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/sse"

    async def start(self) -> None:
        env = dict(os.environ) | {
            "BAND_AGENT_KEY": self._agent_key,
            "BAND_BASE_URL": self._base_url,
            "ALLOWED_HOSTS": ALLOWED_HOSTS,
        }
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            "--transport",
            "sse",
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "--tools",
            TOOL_GROUPS,
            env=env,
        )
        await self._wait_ready()
        logger.info("band-mcp serving on %s", self.sse_url)

    async def stop(self) -> None:
        if self._process is None or self._process.returncode is not None:
            self._process = None
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=STOP_TIMEOUT_S)
        except TimeoutError:
            logger.warning("band-mcp did not terminate in time; killing")
            self._process.kill()
            await self._process.wait()
        self._process = None

    async def restart(self) -> None:
        """Stop and start on the same port — the workspace mcp.json stays valid."""
        await self.stop()
        await self.start()

    async def _wait_ready(self) -> None:
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_S
        while True:
            assert self._process is not None
            if self._process.returncode is not None:
                raise RuntimeError(
                    f"band-mcp exited during startup ({self._process.returncode})"
                )
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", self._port)
            except OSError:
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeError(
                        f"band-mcp not accepting connections on {self._port} "
                        f"after {READY_TIMEOUT_S}s"
                    ) from None
                await asyncio.sleep(0.2)
            else:
                writer.close()
                await writer.wait_closed()
                return
