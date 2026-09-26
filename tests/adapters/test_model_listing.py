"""Model listing and typed model flags for the OMP and Copilot ACP adapters."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

from band.adapters import copilot_acp
from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
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


class FakeCopilotClient:
    """Stands in for ``copilot.CopilotClient``; records its lifecycle."""

    def __init__(self, *, connection: Any, env: dict[str, str] | None) -> None:
        self.connection = connection
        self.env = env
        self.stopped = False
        FakeCopilotClient.last = self

    last: ClassVar[FakeCopilotClient]
    start_error: ClassVar[BaseException | None] = None

    async def start(self) -> None:
        if FakeCopilotClient.start_error is not None:
            raise FakeCopilotClient.start_error

    async def list_models(self) -> list[Any]:
        return [
            SimpleNamespace(
                id="claude-sonnet-5",
                name="Claude Sonnet 5",
                supported_reasoning_efforts=["low", "high"],
                default_reasoning_effort="low",
            )
        ]

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_copilot(monkeypatch: pytest.MonkeyPatch) -> type[FakeCopilotClient]:
    FakeCopilotClient.start_error = None
    monkeypatch.setattr("copilot.CopilotClient", FakeCopilotClient)
    return FakeCopilotClient


async def test_copilot_listing_uses_the_configured_cli_and_auth(fake_copilot) -> None:
    config = CopilotACPAdapterConfig(
        command=("/opt/copilot", "--acp"), github_token="ghp_x"
    )

    models = await copilot_acp.list_models(config)

    assert models == [
        HarnessModel(
            id="claude-sonnet-5",
            label="Claude Sonnet 5",
            efforts=("low", "high"),
            default_effort="low",
        )
    ]
    client = fake_copilot.last
    assert (client.connection.path, client.env["GITHUB_TOKEN"], client.stopped) == (
        "/opt/copilot",
        "ghp_x",
        True,
    )


async def test_copilot_listing_keeps_the_host_environment_under_overrides(
    fake_copilot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAND_TEST_SENTINEL", "inherited")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient")
    config = CopilotACPAdapterConfig(
        github_token="configured", env={"HTTPS_PROXY": "http://proxy:3128"}
    )

    await copilot_acp.list_models(config)

    env = fake_copilot.last.env
    assert (
        env["BAND_TEST_SENTINEL"],
        env["PATH"],
        env["GITHUB_TOKEN"],
        env["HTTPS_PROXY"],
    ) == ("inherited", os.environ["PATH"], "configured", "http://proxy:3128")


async def test_copilot_listing_without_overrides_inherits_as_is(fake_copilot) -> None:
    await copilot_acp.list_models()
    assert fake_copilot.last.env is None


@pytest.mark.parametrize(
    "error", [RuntimeError("spawn failed"), asyncio.CancelledError()]
)
async def test_copilot_listing_stops_a_client_that_failed_to_start(
    fake_copilot, error: BaseException
) -> None:
    fake_copilot.start_error = error

    with pytest.raises(type(error)):
        await copilot_acp.list_models()

    assert fake_copilot.last.stopped


def test_copilot_model_flags_are_appended_once() -> None:
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(model="claude-sonnet-5", reasoning_effort="high")
    )
    assert adapter._command[-4:] == [
        "--model",
        "claude-sonnet-5",
        "--reasoning-effort",
        "high",
    ]
    assert adapter._command.count("--model") == 1


@pytest.mark.parametrize("spliced", ["--model", "--model=gpt-6"])
def test_copilot_model_flag_in_both_places_is_refused(spliced: str) -> None:
    with pytest.raises(ValueError, match="--model is set both"):
        CopilotACPAdapter(
            CopilotACPAdapterConfig(
                command=("copilot", "--acp", spliced), model="claude-sonnet-5"
            )
        )


def test_copilot_model_flags_need_stdio() -> None:
    with pytest.raises(ValueError, match="need the stdio transport"):
        CopilotACPAdapter(
            CopilotACPAdapterConfig(host="10.0.0.5", port=8080, model="gpt-6")
        )
