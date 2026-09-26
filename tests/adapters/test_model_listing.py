"""Model listing and typed model flags for the OMP and Copilot ACP adapters."""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

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
    """An executable that answers ``models --json`` like OMP 18.3.2 does."""
    script = tmp_path / "omp"
    script.write_text(
        "#!/bin/sh\n"
        f"cat <<'JSON'\n{stdout}\nJSON\n"
        f"echo 'omp failed' >&2\nexit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


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


async def test_omp_listing_failure_names_the_command(tmp_path: Path) -> None:
    omp = _fake_omp(tmp_path, stdout="{}", exit_code=3)

    with pytest.raises(RuntimeError, match="models --json` exited 3: omp failed"):
        await omp_list_models(OmpACPAdapterConfig(command=(omp, "acp")))


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
    assert (client.connection.path, client.env, client.stopped) == (
        "/opt/copilot",
        {"GITHUB_TOKEN": "ghp_x"},
        True,
    )


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
