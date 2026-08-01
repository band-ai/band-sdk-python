"""Turn outcome observation — data + asserts, E2E-toolkit style."""

from __future__ import annotations

from dataclasses import dataclass

from tests.baseline.delivery.scenarios import DeliveryScenario


@dataclass(frozen=True)
class TurnOutcome:
    """Observable room outcome after one turn — not adapter internals."""

    texts: tuple[str, ...]
    receipt_tool: str | None

    def shape(self, *, wrap_up: str) -> tuple[str, ...]:
        """Contract-relevant projection: delivered? + wrap-up fallback?"""
        bits: list[str] = []
        if self.receipt_tool is not None:
            bits.append("delivered")
        if wrap_up in self.texts:
            bits.append(f"fallback:{wrap_up}")
        return tuple(bits)

    def assert_matches(self, scenario: DeliveryScenario) -> None:
        """This turn's delivery and visible text exactly match the contract."""
        expected = scenario.expected_shape
        actual = self.shape(wrap_up=scenario.agent_text)
        assert actual == expected, (
            f"{scenario.id}: expected {expected!r}, got {actual!r} "
            f"(receipt_tool={self.receipt_tool!r}, texts={self.texts!r})"
        )
        assert self.texts == scenario.expected_texts, (
            f"{scenario.id}: expected visible texts {scenario.expected_texts!r}, "
            f"got {self.texts!r}"
        )
