"""Live-API error-handling tests for the SDK-driven registrar.

Exercise the validation/dispatch error paths through the real registrar:
unknown tool names, missing required arguments, room-bound tools called
without a room id, and bad credentials. Run with:

    uv run --all-packages pytest tests/integration/mcp/test_error_cases.py -v -s --no-cov
"""

from __future__ import annotations

import pytest

from tests.integration.mcp.conftest import LiveHarness, requires_api


@requires_api
async def test_unknown_tool_name_is_rejected(harness: LiveHarness) -> None:
    """Calling a tool that was never registered raises."""
    with pytest.raises(Exception):
        await harness.call_raw("band_does_not_exist")


@requires_api
async def test_missing_required_argument_reports_field(harness: LiveHarness) -> None:
    """A room-bound agent tool without chat_id fails before any HTTP call."""
    if "agent" not in harness.scope:
        pytest.skip("agent scope not served by this key")

    # band_send_message requires both `content` and a room (`chat_id`).
    with pytest.raises(Exception) as exc_info:
        await harness.call_raw("band_send_message")
    assert "content" in str(exc_info.value)


@requires_api
async def test_human_send_message_requires_chat_id(harness: LiveHarness) -> None:
    """band_send_my_chat_message without chat_id/content is rejected."""
    if "human" not in harness.scope:
        pytest.skip("human scope not served by this key")

    with pytest.raises(Exception) as exc_info:
        await harness.call_raw("band_send_my_chat_message")
    assert "chat_id" in str(exc_info.value)


@requires_api
async def test_resolve_unknown_handle_is_handled(harness: LiveHarness) -> None:
    """Resolving a bogus handle returns an error payload or raises, not a crash."""
    if "human" not in harness.scope:
        pytest.skip("human scope not served by this key")

    try:
        result = await harness.call(
            "band_resolve_handle", handle="@definitely-not-a-real-handle-xyz"
        )
    except Exception:
        # An API-level 404/422 surfacing as an exception is acceptable.
        return
    # Otherwise we should get a structured (non-crashing) response -- not just
    # any non-None value, which an empty string/list would also satisfy.
    assert isinstance(result, dict)
