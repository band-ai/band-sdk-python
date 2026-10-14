"""An image file read reaches the model as something it can ``show()``."""

from __future__ import annotations

from band.core.types import Capability, Emit
from band.runtime.tools import BandTool, image_block_placeholder
from band.testing.nooa import room_call, scripted_llm
from tests.adapters.nooa.kit import (
    image_file_tools,
    prompt_text,
    run_turn,
    started_adapter,
    tool_results,
)


class TestImagePassthrough:
    async def test_an_image_file_comes_back_as_nooa_images(self) -> None:
        tools = image_file_tools()
        llm = scripted_llm(
            f"images = {room_call(BandTool.READ_ROOM_FILE, {'file_id': 'f1'})}\n"
            "print([(image.modality, image.media_type) for image in images])\n"
            f"{room_call(BandTool.NO_REPLY)}"
        )
        adapter = await started_adapter(llm, capabilities={Capability.FILES})

        await run_turn(adapter, tools)

        assert "[('image', 'image/png')]" in prompt_text(llm)

    async def test_the_tool_result_event_carries_a_placeholder_not_bytes(self) -> None:
        tools = image_file_tools()
        llm = scripted_llm(
            f"{room_call(BandTool.READ_ROOM_FILE, {'file_id': 'f1'})}\n"
            f"{room_call(BandTool.NO_REPLY)}"
        )
        adapter = await started_adapter(
            llm, capabilities={Capability.FILES}, emit={Emit.TOOL_CALLS}
        )

        await run_turn(adapter, tools)

        assert tool_results(tools)[0]["output"] == image_block_placeholder(1)
