"""HTTP success is earlier than executor settlement."""

from __future__ import annotations

import asyncio

import httpx

from tests.integrations.a2a.gateway.serverkit import build_server, hello_message_body
from tests.integrations.a2a.gateway.shutdown.kit import HeldExecutor
from tests.lifecycle import running


class TestExecutorSettlement:
    async def test_stop_joins_early_response_executor(self) -> None:
        executor = HeldExecutor()
        server = build_server(port=0, executor_factory=lambda _slug: executor)
        async with running(server):
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://127.0.0.1:{server.bound_port}/agents/weather-agent/message:send",
                    headers={"A2A-Version": "1.0"},
                    json={
                        **hello_message_body(),
                        "configuration": {"returnImmediately": True},
                    },
                )
            response.raise_for_status()
            await executor.entered.wait()
            stopping = asyncio.create_task(server.stop())
            try:
                async with asyncio.timeout(2):
                    await executor.finalizing.wait()
                assert not stopping.done()
            finally:
                executor.release.set()
                await stopping
            assert executor.finished.is_set()
