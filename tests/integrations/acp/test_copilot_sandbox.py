"""The sandbox must have Band tools before starting a paid agent runtime."""

from __future__ import annotations

import runpy
from typing import cast

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from tests.paths import EXAMPLES_ROOT


@pytest.mark.parametrize("config", [{}, {"band_mcp_sse_url": ""}])
def test_sandbox_rejects_missing_band_tools(
    config: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BAND_MCP_SSE_URL", raising=False)
    module = runpy.run_path(
        str(EXAMPLES_ROOT / "acp" / "copilot_sandbox" / "client.py")
    )
    settings = cast(type[BaseSettings], module["Settings"])

    with pytest.raises(ValidationError, match="band_mcp_sse_url"):
        settings(**config)
