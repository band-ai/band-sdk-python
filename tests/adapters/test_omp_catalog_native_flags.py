"""Installed OMP must load configured metadata extensions before or after mode."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from band.adapters.omp import OmpAdapter, OmpAdapterConfig


class ExtensionReceipt(BaseModel):
    models_command: bool


@pytest.mark.asyncio
class TestOmpCatalogNativeFlags:
    @pytest.mark.parametrize("route", ["extension", "config"])
    @pytest.mark.parametrize("before_mode", [False, True])
    @pytest.mark.parametrize("mode_equals", [False, True])
    @pytest.mark.parametrize("launch_position", ["implicit", "first", "last"])
    async def test_native_catalog_loads_configured_extension(
        self,
        tmp_path: Path,
        route: Literal["extension", "config"],
        before_mode: bool,
        mode_equals: bool,
        launch_position: Literal["implicit", "first", "last"],
    ) -> None:
        executable = shutil.which("omp")
        if executable is None:
            pytest.skip("The installed native OMP executable is required")
        extension = (
            Path(__file__).parents[1] / "integrations" / "ompcatalogextension.js"
        )
        receipts = tmp_path / "extension-receipts.jsonl"
        config = tmp_path / "catalog-overlay.yml"
        config.write_text(
            json.dumps({"extensions": [str(extension)]}), encoding="utf-8"
        )
        metadata = (
            ("--extension", str(extension))
            if route == "extension"
            else ("--config", str(config))
        )
        mode = ("--mode=rpc",) if mode_equals else ("--mode", "rpc")
        arguments = (*metadata, *mode) if before_mode else (*mode, *metadata)
        if launch_position == "first":
            arguments = ("launch", *arguments)
        elif launch_position == "last":
            arguments = (*arguments, "launch")
        adapter = OmpAdapter(
            OmpAdapterConfig(
                command=(executable, *arguments),
                cwd=str(tmp_path),
                env={
                    "PI_CODING_AGENT_DIR": str(tmp_path / "native-home"),
                    "BAND_CATALOG_EXTENSION_RECEIPT": str(receipts),
                },
            )
        )
        try:
            catalog = await adapter.probe_models(timeout_s=60)
            fixture = next(
                (
                    model
                    for model in catalog.models
                    if model.id == "band-catalog-fixture/catalog-model"
                ),
                None,
            )
            assert fixture is not None, (
                "Configured native extension did not contribute its model"
            )
            assert fixture.label == "Configured catalog fixture"
            assert fixture.provider == "band-catalog-fixture"
            assert receipts.is_file(), "Native extension never loaded"
            loaded = [
                ExtensionReceipt.model_validate_json(line)
                for line in receipts.read_text(encoding="utf-8").splitlines()
            ]
            assert loaded
            if launch_position == "implicit":
                assert all(receipt.models_command for receipt in loaded)
            if all(receipt.models_command for receipt in loaded):
                assert catalog.current_model is None
            assert not adapter._rooms
        finally:
            await adapter.cleanup_all()
