"""Model listing for the OMP ACP adapter."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

from band.adapters.omp_acp import OmpACPAdapterConfig
from band.adapters.omp_acp import list_models as omp_list_models
from band.core.harness import HarnessModel

_OMP_LISTING = {
    "models": [
        {
            "provider": "anthropic",
            "id": "claude-fable-5",
            "selector": "anthropic/claude-fable-5",
            "name": "Claude Fable 5",
            "thinking": ["low", "max"],
        },
        {"provider": "openai", "id": "gpt-6", "name": "GPT-6", "thinking": None},
    ]
}


def _fake_omp(tmp_path: Path, *, stdout: str, exit_code: int = 0) -> str:
    """An executable that answers ``models --json`` like OMP 18.3.2 does.

    The body runs through the kind of launcher a real install has on each OS
    (an npm ``.cmd`` shim on Windows, an executable script elsewhere), so the
    listing's launcher resolution and spawn are exercised for real.
    """
    body = tmp_path / "fake_omp.py"
    body.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r} + '\\n')\n"
        "sys.stderr.write('omp failed\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    if sys.platform == "win32":
        launcher = tmp_path / "omp.cmd"
        launcher.write_text(f'@"{sys.executable}" "{body}" %*\n')
    else:
        launcher = tmp_path / "omp"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{body}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return str(launcher)


async def test_omp_listing_is_parsed_from_the_cli(tmp_path: Path) -> None:
    omp = _fake_omp(tmp_path, stdout=json.dumps(_OMP_LISTING))

    models = await omp_list_models(OmpACPAdapterConfig(command=(omp, "acp")))

    assert models == [
        HarnessModel(
            id="anthropic/claude-fable-5",
            label="Claude Fable 5",
            provider="anthropic",
            efforts=("low", "max"),
        ),
        HarnessModel(id="gpt-6", label="GPT-6", provider="openai"),
    ]


def _select(
    option_id: str, current: str, values: list[str]
) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id=option_id,
        name=option_id,
        type="select",
        current_value=current,
        options=[SessionConfigSelectOption(value=v, name=v.upper()) for v in values],
    )


class FakeOmpSession:
    """A spawn seam for the ACP fallback: one session advertising ``options``."""

    def __init__(
        self, options: list[Any], *, new_session_error: BaseException | None = None
    ):
        self.conn = AsyncMock()
        self.conn.initialize = AsyncMock(return_value=MagicMock())
        self.conn.new_session = AsyncMock(
            side_effect=new_session_error,
            return_value=SimpleNamespace(session_id="probe", config_options=options),
        )
        self.exited = False

    def __call__(self, client: Any, *args: Any, **kwargs: Any) -> Any:
        session = self

        class Ctx:
            async def __aenter__(self) -> tuple[Any, Any]:
                return session.conn, MagicMock()

            async def __aexit__(self, *exc: object) -> None:
                session.exited = True

        return Ctx()


@pytest.fixture
def omp_without_listing(tmp_path: Path) -> OmpACPAdapterConfig:
    """An ``omp`` whose ``models --json`` subcommand fails."""
    omp = _fake_omp(tmp_path, stdout="unknown command: models", exit_code=2)
    return OmpACPAdapterConfig(command=(omp, "acp"))


async def test_omp_falls_back_to_the_acp_session_selects(
    omp_without_listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = FakeOmpSession(
        [
            _select("model", "openai/gpt-6", ["anthropic/claude-5", "openai/gpt-6"]),
            _select("thinking", "auto", ["off", "auto", "high"]),
        ]
    )
    monkeypatch.setattr(
        "band.integrations.acp.client_runtime.spawn_agent_process", spawn
    )

    models = await omp_list_models(omp_without_listing)

    assert models == [
        HarnessModel(
            id="anthropic/claude-5", label="ANTHROPIC/CLAUDE-5", provider="anthropic"
        ),
        HarnessModel(
            id="openai/gpt-6",
            label="OPENAI/GPT-6",
            provider="openai",
            efforts=("off", "auto", "high"),
            default_effort="auto",
            is_default=True,
        ),
    ]
    assert spawn.exited


async def test_omp_fallback_closes_its_session_process_on_cancellation(
    omp_without_listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn = FakeOmpSession([], new_session_error=asyncio.CancelledError())
    monkeypatch.setattr(
        "band.integrations.acp.client_runtime.spawn_agent_process", spawn
    )

    with pytest.raises(asyncio.CancelledError):
        await omp_list_models(omp_without_listing)
    assert spawn.exited
