"""BaselineTools dispatch must hand adapters the real SDK result shapes."""

from __future__ import annotations

from tests.baseline.tools import BaselineTools


async def test_memory_roundtrip_uses_real_sdk_envelope(
    baseline_tools: BaselineTools,
) -> None:
    await baseline_tools.execute_tool_call(
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

    assert set(listing) == {"data", "meta"}, (
        f"Dispatch envelope keys {set(listing)} drifted from the real SDK's "
        "{data, meta} — adapters under baseline tests would see a fake-only shape"
    )
    assert listing["data"][0]["content"] == "Baseline preference", (
        "A memory stored through dispatch must flow back through the listing"
    )
    assert listing["meta"] == {"page_size": 1, "total_count": 1}, (
        "meta must report this page's size and the total match count"
    )
