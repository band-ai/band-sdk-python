"""ImagePassthrough behavior."""

from __future__ import annotations

import pytest

from band.core.types import Capability
from tests.framework_conformance.imageprobes import (
    _CREWAI_AVAILABLE,
    IMAGE_PASSTHROUGH_PROBES,
    ReadRoomFileTools,
    _framework_param,
)
from tests.framework_conformance.test_adapter_conformance import (
    IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS,
)


class TestImagePassthrough:
    def test_probe_registry_matches_supported_framework_ids(self) -> None:
        """The probes are the live proof behind the supported-framework set."""
        assert (
            set(IMAGE_PASSTHROUGH_PROBES) == IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS
        )

    @pytest.mark.skipif(
        not _CREWAI_AVAILABLE, reason="crewai not installed in this venv"
    )
    def test_crewai_platform_tool_name_is_plain_str(self) -> None:
        """CrewAI's real BaseTool doesn't validate field defaults, so a bare
        BandTool (StrEnum) default would leave tool.name a BandTool instance at
        runtime instead of the str the field is typed as -- str(spec.name) at
        the PlatformTool definition must keep it a plain str."""
        from band.integrations.crewai.tools import (  # noqa: PLC0415 -- crewai extra, absent from the standard dev-crewai/dev-parlant lane venvs
            CrewAIToolContext,
            NoopReporter,
            build_band_crewai_tools,
        )

        context = CrewAIToolContext(room_id="room-1", tools=ReadRoomFileTools())
        tools = build_band_crewai_tools(
            get_context=lambda: context,
            reporter=NoopReporter(),
            capabilities=frozenset({Capability.FILES}),
        )

        for tool in tools:
            assert type(tool.name) is str, (
                f"{tool.name!r} is {type(tool.name)}, not plain str"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "framework_id",
        [_framework_param(fid) for fid in sorted(IMAGE_PASSTHROUGH_PROBES)],
    )
    async def test_image_result_passes_through_as_real_content(
        self, framework_id: str
    ) -> None:
        probe = IMAGE_PASSTHROUGH_PROBES[framework_id]

        assert await probe(), (
            f"{framework_id} did not pass an image band_read_room_file result "
            "through as real image content"
        )
