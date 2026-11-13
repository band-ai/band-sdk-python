"""The installed CLI smoke exercises SDK dispatch and fails on resolver errors."""

from __future__ import annotations

import runpy
from http import HTTPStatus

import pytest

from tests.paths import CI_SCRIPTS


class TestWheelSmoke:
    async def test_cli_dispatch_reaches_authenticated_platform_peer(self) -> None:
        smoke = runpy.run_path(str(CI_SCRIPTS / "mcp-wheel-smoke.py"))
        await smoke["main"]()

    async def test_identity_failure_rejects_cli_smoke(self) -> None:
        smoke = runpy.run_path(str(CI_SCRIPTS / "mcp-wheel-smoke.py"))
        async with smoke["platform_peer"](HTTPStatus.UNAUTHORIZED) as (
            base_url,
            requests,
        ):
            with pytest.RaisesGroup(
                pytest.RaisesExc(AssertionError, match="isError=True"),
                flatten_subgroups=True,
            ):
                await smoke["exercise_cli"](base_url)
            assert requests == [smoke["IDENTITY_REQUEST"]]
