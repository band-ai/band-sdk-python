"""Batch-input probe for the NOOA adapter; see ``batchprobes``.

The adapter runs NOOA's real agent loop; only the model is scripted, through
NOOA's own ``FakeLLMClient``. NOOA ends a turn with ``return_result`` and replies
through Band tools, so there is no final text for the model to end with or for
the adapter to relay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput
from tests.framework_configs.adapters import ADAPTER_EXCLUDED_MODULES
from tests.framework_conformance.batchprobes import BatchProbe, deliver

if TYPE_CHECKING:
    from nooa.unifiedllm import FakeLLMClient


async def recording_adapter(
    turns: int,
) -> tuple[SimpleAdapter[list[str]], FakeLLMClient]:
    """A started adapter whose model ends each of ``turns`` agent calls without
    acting, and that model."""
    pytest.importorskip("nooa")
    from nooa.unifiedllm import (  # noqa: PLC0415 -- nooa extra, POSIX-only: absent from some lane venvs
        FakeLLMClient,
    )

    from band.adapters.nooa import (  # noqa: PLC0415 -- nooa extra, POSIX-only: absent from some lane venvs
        NooaAdapter,
    )
    from band.testing.nooa import (  # noqa: PLC0415 -- nooa extra, POSIX-only: absent from some lane venvs
        result_turn,
        room_agent_class,
    )

    llm = FakeLLMClient([result_turn() for _ in range(turns)])
    adapter = NooaAdapter(agent=room_agent_class(llm))
    await adapter.on_started("Agent", "A test agent")
    return adapter, llm


def model_input(llm: FakeLLMClient) -> str:
    """Everything the model was sent on its latest call, minus NOOA's
    triple-quote framing of the message argument, whose closing quotes land on
    the last record's line."""
    prompt = "\n".join(str(message.get("content")) for message in llm.last_messages)
    return prompt.replace("}'''", "}")


async def run_nooa(inp: AgentInput, final_text: str) -> str:
    del final_text
    adapter, llm = await recording_adapter(turns=1)
    await deliver(adapter, inp)
    return model_input(llm)


PROBES: dict[str, BatchProbe] = (
    {"nooa": BatchProbe(run=run_nooa)} if "nooa" not in ADAPTER_EXCLUDED_MODULES else {}
)
