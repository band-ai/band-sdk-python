"""A tool call refused for its credential never runs the tool."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from tests.mcp.loopbackauth.kit import (
    RECORD_TOOL,
    REFUSED_CREDENTIALS,
    SSE_MESSAGE,
    Credential,
    guarded_servers,
    tool_call,
)


class TestRefusedCalls:
    @pytest.mark.timeout(90)
    @pytest.mark.parametrize("credential", REFUSED_CREDENTIALS)
    async def test_refused_call_on_a_live_session_runs_no_tool(
        self, credential: Credential
    ) -> None:
        """The session is initialized, so only the credential stands between
        the refused call and the tool; the authorized call after it runs."""
        async with guarded_servers() as guarded:
            status = await guarded.status(SSE_MESSAGE, credential, tool_call("refused"))
            await guarded.session.call_tool(RECORD_TOOL, {"note": "accepted"})

        assert (status, guarded.recorded) == (HTTPStatus.UNAUTHORIZED, ["accepted"])
