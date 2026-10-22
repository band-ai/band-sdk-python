"""Exercise an installed native structured ask without a Band room or inference.

Run with ``python -m tests.integrations.ompasksmoke --executable /path/to/omp``.
The native extension command validates the real wire answer and writes a receipt.
The model selection enables RPC startup only; the command never invokes it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from band.core.defaultmodels import ANTHROPIC_SMALL_MODEL
from band.integrations.omproomask import delivered_answers
from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEvent,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)


async def smoke(executable: str, timeout_s: float) -> None:
    asked: list[OmpUIRequest] = []

    async def on_event(event: OmpEvent) -> None:
        assert event.type != "agent_start", "The wire smoke must not spend inference"

    async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
        raise AssertionError(f"Unexpected host tool: {call.toolName}")

    async def on_ui(request: OmpUIRequest) -> OmpUIResponse:
        assert request.method == "ask", f"Unexpected UI method: {request.method}"
        asked.append(request)
        return OmpUIResponse(answers=delivered_answers(request.questions))

    with tempfile.TemporaryDirectory(prefix="band-omp-ask-wire-") as sandbox:
        receipt = Path(sandbox) / "answer.json"
        client = OmpRpcClient(
            (
                executable,
                "--mode",
                "rpc",
                "--model",
                f"anthropic/{ANTHROPIC_SMALL_MODEL}",
                "--no-session",
                f"--cwd={sandbox}",
                "--no-extensions",
                f"--extension={Path(__file__).with_suffix('.js')}",
            ),
            env={"HOME": sandbox, "BAND_OMP_ASK_SMOKE_RECEIPT": str(receipt)},
        )
        try:
            async with asyncio.timeout(timeout_s):
                await client.start()
                assert await client.request("set_ask_dialog", enabled=True) == {
                    "enabled": True
                }
                await client.prompt(
                    "/band-ask-wire", on_event=on_event, on_tool=on_tool, on_ui=on_ui
                )
                [request] = asked
                assert [question.id for question in request.questions] == [
                    "storage",
                    "targets",
                ]
                assert request.questions[0].recommended == 0
                assert request.questions[1].multi
                answer = json.loads(receipt.read_text(encoding="utf-8"))
                assert answer["kind"] == "submit"
                assert [result["id"] for result in answer["results"]] == [
                    "storage",
                    "targets",
                ]
                assert all(
                    result["selectedOptions"] == [] for result in answer["results"]
                )
        finally:
            await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default="omp")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    args = parser.parse_args()
    asyncio.run(smoke(args.executable, args.timeout_s))


if __name__ == "__main__":
    main()
