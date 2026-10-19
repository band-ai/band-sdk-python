from __future__ import annotations

from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import ResultMessage


class TestClaude:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("result,is_error", [("OAUTH_OK", False), (None, True)])
    async def test_claude_check_drains_the_sdk_before_finishing(
        self,
        bridge: ModuleType,
        options: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        result: str | None,
        is_error: bool,
    ) -> None:
        options.check_claude = True
        drained = []
        sessions = []

        async def query(**kwargs: Any) -> AsyncIterator[ResultMessage]:
            sessions.append(kwargs["options"])
            yield bridge.ResultMessage(
                subtype="success",
                duration_ms=0,
                duration_api_ms=0,
                is_error=is_error,
                num_turns=1,
                session_id="test-session",
                result=result,
            )
            drained.append(True)

        monkeypatch.setattr(bridge, "query", query)
        if is_error:
            with pytest.raises(ValueError, match="authentication check failed"):
                await bridge.main(options)
        else:
            await bridge.main(options)
        assert drained == [True]
        assert sessions[0].cli_path == str(options.workspace / "claude")
        assert sessions[0].cwd == str(options.workspace)

    @pytest.mark.asyncio
    async def test_empty_sdk_stream_is_not_success(
        self,
        bridge: ModuleType,
        options: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        options.check_claude = True

        async def query(**kwargs: Any) -> AsyncIterator[ResultMessage]:
            for message in ():
                yield message

        monkeypatch.setattr(bridge, "query", query)
        with pytest.raises(ValueError, match="returned no result"):
            await bridge.main(options)
