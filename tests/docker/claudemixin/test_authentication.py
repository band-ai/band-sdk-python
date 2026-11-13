from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest
from pytest_httpx import HTTPXMock
from pytest_mock import MockerFixture


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_band_check_sends_no_room_messages(
        self, bridge: ModuleType, options: SimpleNamespace, httpx_mock: HTTPXMock
    ) -> None:
        options.check_band = True
        httpx_mock.add_response(
            json={"data": {"id": "test-agent", "handle": "owner/agent"}}
        )
        await bridge.main(options)
        requests = httpx_mock.get_requests()
        assert [(r.method, str(r.url)) for r in requests] == [
            ("GET", "https://app.band.ai/api/v1/agent/me")
        ]
        assert requests[0].headers["X-API-Key"] == "unit-test-key"

    @pytest.mark.asyncio
    async def test_wrong_identity_never_starts_a_receiver(
        self,
        bridge: ModuleType,
        options: SimpleNamespace,
        httpx_mock: HTTPXMock,
        mocker: MockerFixture,
    ) -> None:
        start = mocker.patch.object(bridge.Agent, "create")
        httpx_mock.add_response(
            json={"data": {"id": "other-agent", "handle": "owner/other"}}
        )
        with pytest.raises(ValueError, match="different agent UUID"):
            await bridge.main(options)
        start.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_key_never_starts_a_receiver(
        self,
        bridge: ModuleType,
        options: SimpleNamespace,
        httpx_mock: HTTPXMock,
        mocker: MockerFixture,
    ) -> None:
        start = mocker.patch.object(bridge.Agent, "create")
        httpx_mock.add_response(status_code=401)
        with pytest.raises(bridge.httpx.HTTPStatusError):
            await bridge.main(options)
        start.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_claude_reports_the_workload_prerequisite(
        self,
        bridge: ModuleType,
        options: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(bridge.shutil, "which", lambda _: None)
        with pytest.raises(ValueError, match="workload that supplies Claude Code"):
            await bridge.main(options)
