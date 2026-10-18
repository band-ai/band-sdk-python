"""Hosts discover the configured Copilot account's models without a turn."""

from __future__ import annotations

import asyncio
import builtins
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig
from band.core.exceptions import BandConfigError, CleanupIncompleteError
from band.core.model_catalog import ModelCatalog
from tests.adapters.copilotprobe import CopilotProbeSDK


@pytest.fixture
def copilot_probe_sdk(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> CopilotProbeSDK:
    module = pytest.importorskip("copilot")
    cli_dir = tmp_path_factory.mktemp("copilot-cli")
    executable = cli_dir / ("copilot.exe" if os.name == "nt" else "copilot")
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(cli_dir))
    sdk = CopilotProbeSDK(module)
    sdk.models = [
        module.ModelInfo.from_dict(
            {
                "id": "reasoning-model",
                "name": "Reasoning model",
                "capabilities": {"supports": {"reasoningEffort": True}, "limits": {}},
                "supportedReasoningEfforts": ["low", "high"],
                "defaultReasoningEffort": "high",
            }
        ),
        module.ModelInfo.from_dict(
            {
                "id": "unknown-efforts",
                "name": "Unknown efforts",
                "capabilities": {"supports": {"reasoningEffort": True}, "limits": {}},
            }
        ),
        module.ModelInfo.from_dict(
            {
                "id": "no-efforts",
                "name": "No efforts",
                "capabilities": {"supports": {}, "limits": {}},
                "supportedReasoningEfforts": [],
            }
        ),
    ]
    monkeypatch.setattr(module, "CopilotClient", sdk.factory)
    return sdk


class TestCopilotModelProbe:
    async def test_returns_canonical_catalog_from_current_sdk_models(
        self, copilot_probe_sdk: CopilotProbeSDK, tmp_path: Path
    ) -> None:
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(cwd=str(tmp_path)))

        catalog = await adapter.probe_models(timeout_s=1)

        assert isinstance(catalog, ModelCatalog)
        assert [choice.model_dump() for choice in catalog.models] == [
            {
                "id": "reasoning-model",
                "label": "Reasoning model",
                "provider": None,
                "is_default": False,
                "efforts": ("low", "high"),
                "default_effort": "high",
            },
            {
                "id": "unknown-efforts",
                "label": "Unknown efforts",
                "provider": None,
                "is_default": False,
                "efforts": None,
                "default_effort": None,
            },
            {
                "id": "no-efforts",
                "label": "No efforts",
                "provider": None,
                "is_default": False,
                "efforts": (),
                "default_effort": None,
            },
        ]
        assert catalog.current_model is None
        assert copilot_probe_sdk.open_clients == set()
        assert adapter._rooms == {}
        assert not tuple(tmp_path.iterdir())
        copilot_probe_sdk.clients[0].create_session.assert_not_awaited()

    @pytest.mark.parametrize(
        ("env", "shortcut", "expected"),
        [
            ({}, None, None),
            ({}, "shortcut", "shortcut"),
            ({"GITHUB_TOKEN": "configured"}, "shortcut", "configured"),
            ({"GH_TOKEN": "higher"}, "shortcut", "higher"),
            (
                {"COPILOT_GITHUB_TOKEN": "highest", "GH_TOKEN": "higher"},
                "shortcut",
                "highest",
            ),
        ],
    )
    async def test_preserves_configured_token_precedence_and_environment(
        self,
        copilot_probe_sdk: CopilotProbeSDK,
        monkeypatch: pytest.MonkeyPatch,
        env: dict[str, str],
        shortcut: str | None,
        expected: str | None,
    ) -> None:
        monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "unrelated-host-token")
        monkeypatch.setenv("GH_TOKEN", "unrelated-gh-token")
        monkeypatch.setenv("GITHUB_TOKEN", "unrelated-github-token")
        configured_env = {"EXTRA_SETTING": "enabled", **env}
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(github_token=shortcut, env=configured_env)
        )

        await adapter.probe_models(timeout_s=1)

        options = copilot_probe_sdk.launches[0]
        assert options.get("github_token") == expected
        assert options["env"]["EXTRA_SETTING"] == "enabled"
        assert "unrelated-host-token" not in options["env"].values()
        assert "unrelated-gh-token" not in options["env"].values()
        assert "unrelated-github-token" not in options["env"].values()
        for name, value in configured_env.items():
            assert options["env"][name] == value

    async def test_uses_the_configured_executable_args_and_root_directory(
        self, copilot_probe_sdk: CopilotProbeSDK, tmp_path: Path
    ) -> None:
        executable = str(Path(sys.executable).resolve())
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(
                command=(executable, "--custom-flag", "--acp", "--another-flag"),
                cwd=str(tmp_path),
            )
        )

        await adapter.probe_models(timeout_s=1)

        options = copilot_probe_sdk.launches[0]
        assert options["connection"].path == executable
        assert tuple(options["connection"].args) == ("--custom-flag", "--another-flag")
        assert options["working_directory"] == str(tmp_path)
        assert not tuple(tmp_path.iterdir())

    @pytest.mark.parametrize(
        "selection", ["absolute-path", "relative-path", "relative-command"]
    )
    async def test_selects_executable_from_configured_environment_and_cwd(
        self,
        selection: str,
        copilot_probe_sdk: CopilotProbeSDK,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        root = tmp_path / "configured-root"
        configured_bin = root / "bin"
        ambient_bin = tmp_path / "ambient-bin"
        configured_bin.mkdir(parents=True)
        ambient_bin.mkdir()
        name = "copilot-custom.exe" if os.name == "nt" else "copilot-custom"
        selected = configured_bin / name
        for executable in (selected, ambient_bin / name):
            executable.write_text("", encoding="utf-8")
            executable.chmod(0o755)
        monkeypatch.setenv("PATH", str(ambient_bin))
        command = str(Path("bin") / name) if selection == "relative-command" else name
        env = (
            {}
            if selection == "relative-command"
            else {
                "PATH": str(configured_bin) if selection == "absolute-path" else "bin"
            }
        )
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(command=(command, "--acp"), env=env, cwd=str(root))
        )

        await adapter.probe_models(timeout_s=1)

        assert copilot_probe_sdk.launches[0]["connection"].path == str(
            selected.resolve()
        )

    async def test_missing_configured_executable_does_not_fall_back_to_ambient_path(
        self,
        copilot_probe_sdk: CopilotProbeSDK,
        tmp_path: Path,
    ) -> None:
        configured_bin = tmp_path / "empty-bin"
        configured_bin.mkdir()
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(env={"PATH": str(configured_bin)})
        )

        with pytest.raises(BandConfigError):
            await adapter.probe_models(timeout_s=1)

        assert copilot_probe_sdk.clients == []

    async def test_does_not_use_the_acp_preflight_or_claim_a_room(
        self, copilot_probe_sdk: CopilotProbeSDK, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def room_workspace(room_id: str) -> str:
            pytest.fail(f"model probe claimed a Band room: {room_id}")

        adapter = CopilotACPAdapter(workspace_for_room=room_workspace)
        monkeypatch.setattr(
            adapter,
            "preflight",
            AsyncMock(side_effect=AssertionError("ACP preflight")),
            raising=False,
        )

        assert (await adapter.probe_models(timeout_s=1)).model_ids == (
            "reasoning-model",
            "unknown-efforts",
            "no-efforts",
        )
        assert copilot_probe_sdk.close_attempts == 1

    async def test_distinguishes_empty_account_catalog_from_unsupported(
        self, copilot_probe_sdk: CopilotProbeSDK
    ) -> None:
        copilot_probe_sdk.models = []
        adapter = CopilotACPAdapter()

        catalog = await adapter.probe_models(timeout_s=1)

        assert catalog.models == ()
        assert catalog.current_model is None
        assert copilot_probe_sdk.open_clients == set()

    async def test_missing_optional_sdk_does_not_break_acp_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_import = builtins.__import__

        def importing(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "copilot" or name.startswith("copilot."):
                raise ModuleNotFoundError("No module named 'copilot'", name="copilot")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", importing)
        adapter = CopilotACPAdapter()
        with pytest.raises(BandConfigError, match="copilot_sdk"):
            await adapter.probe_models(timeout_s=1)

    @pytest.mark.parametrize("phase", ["start", "list"])
    async def test_failure_is_useful_and_does_not_expose_credentials(
        self, phase: str, copilot_probe_sdk: CopilotProbeSDK
    ) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(github_token="plain-secret-credential")
        )
        setattr(
            copilot_probe_sdk,
            f"{phase}_error",
            RuntimeError(
                "plain-secret-credential https://private.provider.invalid/api"
            ),
        )

        with pytest.raises(BandConfigError) as raised:
            await adapter.probe_models(timeout_s=1)

        assert str(raised.value)
        assert "plain-secret-credential" not in str(raised.value)
        assert "private.provider.invalid" not in str(raised.value)
        assert raised.value.__cause__ is None
        assert copilot_probe_sdk.open_clients == set()

    async def test_keeps_failed_close_until_adapter_cleanup(
        self, copilot_probe_sdk: CopilotProbeSDK
    ) -> None:
        adapter = CopilotACPAdapter()
        copilot_probe_sdk.close_failures = 1

        with pytest.raises(CleanupIncompleteError):
            await adapter.probe_models(timeout_s=0.1)
        assert len(copilot_probe_sdk.open_clients) == 1

        await adapter.cleanup_all()

        assert copilot_probe_sdk.open_clients == set()
        assert (await adapter.probe_models(timeout_s=1)).model_ids[
            0
        ] == "reasoning-model"

    async def test_does_not_lose_cleanup_failure_when_listing_also_fails(
        self, copilot_probe_sdk: CopilotProbeSDK
    ) -> None:
        adapter = CopilotACPAdapter()
        copilot_probe_sdk.list_error = RuntimeError("listing refused")
        copilot_probe_sdk.close_failures = 1

        with pytest.raises(CleanupIncompleteError):
            await adapter.probe_models(timeout_s=0.1)
        assert len(copilot_probe_sdk.open_clients) == 1

        await adapter.cleanup_all()

        assert copilot_probe_sdk.open_clients == set()

    @pytest.mark.looptime
    async def test_timeout_closes_client_and_next_probe_still_works(
        self,
        copilot_probe_sdk: CopilotProbeSDK,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Windows' native timer resolution can exceed this virtual deadline.
        monkeypatch.setattr(asyncio.get_running_loop(), "_clock_resolution", 1e-6)
        adapter = CopilotACPAdapter()
        copilot_probe_sdk.hold_start = True
        with pytest.raises(BandConfigError):
            await adapter.probe_models(timeout_s=0.01)
        assert copilot_probe_sdk.started.is_set()
        assert copilot_probe_sdk.open_clients == set()

        copilot_probe_sdk.hold_start = False
        assert (await adapter.probe_models(timeout_s=1)).models

    async def test_cancellation_propagates_and_releases_the_client(
        self, copilot_probe_sdk: CopilotProbeSDK
    ) -> None:
        adapter = CopilotACPAdapter()
        copilot_probe_sdk.hold_start = True
        task = asyncio.create_task(adapter.probe_models(timeout_s=1))
        await copilot_probe_sdk.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert copilot_probe_sdk.open_clients == set()

    async def test_owns_client_whose_start_finishes_after_timeout(
        self, copilot_probe_sdk: CopilotProbeSDK
    ) -> None:
        adapter = CopilotACPAdapter()
        copilot_probe_sdk.hold_start = True
        copilot_probe_sdk.ignore_start_cancellation = True
        try:
            with pytest.raises(CleanupIncompleteError):
                await adapter.probe_models(timeout_s=0.01)
            assert len(copilot_probe_sdk.open_clients) == 1
            copilot_probe_sdk.release_start.set()

            await adapter.cleanup_all()

            assert copilot_probe_sdk.open_clients == set()
        finally:
            copilot_probe_sdk.release_start.set()
            await adapter.cleanup_all()
