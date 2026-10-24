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
    OmpCommandName,
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIMethod,
    OmpUIRequest,
    OmpUIResponse,
)


class AskSmokeObserver:
    """Record native ask requests and reject inference or host-tool work."""

    def __init__(self) -> None:
        self.requests: list[OmpUIRequest] = []

    async def on_event(self, event: OmpEvent) -> None:
        assert event.type != OmpEventType.AGENT_START, (
            "The wire smoke must not spend inference"
        )

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        raise AssertionError(f"Unexpected host tool: {call.toolName}")

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        assert request.method == OmpUIMethod.ASK, (
            f"Unexpected UI method: {request.method}"
        )
        self.requests.append(request)
        return OmpUIResponse(answers=delivered_answers(request.questions))

    def assert_exchange(self, receipt: Path) -> None:
        [request] = self.requests
        question_ids = [question.id for question in request.questions]
        assert question_ids == ["storage", "targets"]
        assert request.questions[0].recommended == 0
        assert request.questions[1].multi
        answer = json.loads(receipt.read_text(encoding="utf-8"))
        assert answer["kind"] == "submit"
        assert [result["id"] for result in answer["results"]] == question_ids
        assert all(result["selectedOptions"] == [] for result in answer["results"])


async def smoke(executable: str, timeout_s: float) -> None:
    observer = AskSmokeObserver()
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
                assert await client.request(
                    OmpCommandName.SET_ASK_DIALOG, enabled=True
                ) == {"enabled": True}
                await client.prompt(
                    "/band-ask-wire",
                    on_event=observer.on_event,
                    on_tool=observer.on_tool,
                    on_ui=observer.on_ui,
                )
                observer.assert_exchange(receipt)
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
