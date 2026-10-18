"""Claude metadata becomes a canonical catalog without inventing capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.core.exceptions import BandConfigError
from band.core.model_catalog import ModelCatalog, ModelChoice
from tests.adapters.claude_sdk.hostprobe import host_probe
from tests.adapters.claude_sdk.probepeer import PRIVATE_ERROR, ProbeMode


async def test_model_probe_preserves_unknown_efforts_and_advertised_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = host_probe(
        monkeypatch,
        tmp_path,
        info={
            "models": [
                {
                    "value": "default",
                    "displayName": "Default",
                    "supportedEffortLevels": ["low", "high"],
                },
                {"value": "legacy", "displayName": "Legacy"},
                {"value": "no-effort", "supportedEffortLevels": []},
                {"value": "disabled", "supportsEffort": False},
            ]
        },
    )
    try:
        catalog = await probe.adapter.probe_models(timeout_s=5)
        assert catalog == ModelCatalog(
            models=(
                ModelChoice(
                    id="default",
                    label="Default",
                    efforts=("low", "high"),
                    is_default=True,
                ),
                ModelChoice(id="legacy", label="Legacy", efforts=None),
                ModelChoice(id="no-effort", label="no-effort", efforts=()),
                ModelChoice(id="disabled", label="disabled", efforts=()),
            )
        )
        [child] = probe.children
        assert child.exited
        [frame] = probe.frames()
        assert frame["message"]["request"]["subtype"] == "initialize"
        assert probe.adapter._session_manager is None
    finally:
        await probe.adapter.cleanup_all()


async def test_an_advertised_empty_model_list_is_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = host_probe(monkeypatch, tmp_path, info={"models": []})
    try:
        assert await probe.adapter.probe_models(timeout_s=5) == ModelCatalog(models=())
        [child] = probe.children
        assert child.exited
    finally:
        await probe.adapter.cleanup_all()


@pytest.mark.parametrize("info", [{}, {"models": None}, {"models": "not a list"}])
async def test_unavailable_model_metadata_is_not_reported_as_an_empty_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, info: dict[str, object]
) -> None:
    probe = host_probe(monkeypatch, tmp_path, info=info)
    try:
        with pytest.raises(BandConfigError):
            await probe.adapter.probe_models(timeout_s=5)
        [child] = probe.children
        assert child.exited
    finally:
        await probe.adapter.cleanup_all()


async def test_malformed_model_metadata_does_not_echo_private_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = host_probe(
        monkeypatch, tmp_path, info={"models": [{"value": {"token": PRIVATE_ERROR}}]}
    )
    try:
        with pytest.raises(BandConfigError) as raised:
            await probe.adapter.probe_models(timeout_s=5)
        assert PRIVATE_ERROR not in str(raised.value)
        [child] = probe.children
        assert child.exited
    finally:
        await probe.adapter.cleanup_all()


async def test_model_handshake_failure_is_safe_and_still_closes_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = host_probe(monkeypatch, tmp_path, mode=ProbeMode.ERROR)
    try:
        with pytest.raises(BandConfigError) as raised:
            await probe.adapter.probe_models(timeout_s=5)
        assert PRIVATE_ERROR not in str(raised.value)
        [child] = probe.children
        assert child.exited
    finally:
        await probe.adapter.cleanup_all()
