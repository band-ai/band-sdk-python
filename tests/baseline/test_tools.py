"""BaselineTools dispatch must hand adapters the real SDK result shapes."""

from __future__ import annotations

from tests.baseline.tools import BaselineTools


async def test_memory_roundtrip_uses_real_sdk_envelope(
    baseline_tools: BaselineTools,
) -> None:
    stored = await baseline_tools.execute_tool_call(
        "band_store_memory",
        {
            "content": "Baseline preference",
            "system": "long_term",
            "type": "semantic",
            "segment": "user",
            "thought": "remember it",
            "scope": "organization",
        },
    )

    listing = await baseline_tools.execute_tool_call("band_list_memories", {})
    fetched = await baseline_tools.execute_tool_call(
        "band_get_memory", {"memory_id": stored["id"]}
    )
    archived = await baseline_tools.execute_tool_call(
        "band_archive_memory", {"memory_id": stored["id"]}
    )

    assert listing["data"][0]["content"] == "Baseline preference", (
        "A memory stored through dispatch must flow back through the listing"
    )
    assert fetched["content"] == "Baseline preference", (
        "band_get_memory must retrieve the memory band_store_memory stored"
    )
    assert archived["status"] == "archived", (
        "band_archive_memory must mutate the stored memory's status"
    )
