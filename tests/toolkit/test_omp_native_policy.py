"""Keep the live permission probe from granting unrelated native tools."""

import pytest

from band.adapters.omp import OmpPermissionRequest
from tests.e2e.baseline.smoke.samples.ompapproval import (
    FORM_PROBE_TITLE,
    deny_probe_allow_completion,
)


@pytest.mark.parametrize(
    ("title", "options", "expected"),
    [
        (
            "Allow tool: band_send_message\nArguments: {}",
            ("Approve", "Deny"),
            "Approve",
        ),
        ("Allow tool: band_no_reply\nArguments: {}", ("Approve", "Deny"), "Approve"),
        ("Allow tool: write\nArguments: band_send_message", ("Approve", "Deny"), None),
        ("Allow tool: eval", ("Approve", "Deny"), None),
        ("Allow tool: band_send_event", ("Approve", "Deny"), None),
        ("Allow tool: mcp__other_band_send_message", ("Approve", "Deny"), None),
        ("Allow tool: band_send_message suffix", ("Approve", "Deny"), None),
        ("Description\nAllow tool: band_send_message", ("Approve", "Deny"), None),
        ("Allow tool: band_send_message\r\nArguments: {}", ("Approve", "Deny"), None),
        ("Allow tool: band_send_message", ("Always", "Deny"), None),
    ],
)
async def test_probe_policy_grants_only_named_completion(
    title: str,
    options: tuple[str, ...],
    expected: str | None,
) -> None:
    request = OmpPermissionRequest(
        room_id="room",
        session_id="session",
        title=title,
        options=options,
    )
    assert await deny_probe_allow_completion(request, probe_decision="Deny") == expected


@pytest.mark.parametrize("decision", ["Deny", None])
async def test_probe_policy_preserves_denial_or_cancellation(
    decision: str | None,
) -> None:
    probe = OmpPermissionRequest(
        room_id="room",
        session_id="session",
        title=FORM_PROBE_TITLE,
        options=("Approve", "Deny"),
    )
    assert await deny_probe_allow_completion(probe, probe_decision=decision) == decision
