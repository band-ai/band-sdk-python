"""A host-selected Claude settings file acts during a real room turn.

The live-platform companion to ``tests/integration/test_claude_sdk_settings_native.py``:
through the full ``Agent`` stack, the real CLI runs the selected file's SessionStart
hook while the room still gets its Band reply — so the typed option cannot have been
wired at the cost of the tools, and the settings' native effect is observed rather
than echoed. Gated by the registry's Anthropic requirement like every claude cell.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.baseline.agents import Adapter, per_adapter
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=1)


class TestClaudeSdkSettings:
    @per_adapter(Adapter.CLAUDE_SDK, prompt=REPLY_PROMPT)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_selected_settings_file_acts_while_the_room_gets_its_reply(
        self,
        cell: AdapterCell,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
    ) -> None:
        """Typed settings ride the live room session: the CLI acts on the selected
        file (its SessionStart hook writes the marker) and the Band reply still
        arrives — both halves, or the cell fails."""
        hook_marker = tmp_path / "session-start-marker"
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"touch {hook_marker}",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        identity = await cell.provision(label=f"claude-settings-{cell.adapter_id}")
        room_id = await resource_manager.provision_room(
            title=f"e2e-claude-settings-{cell.adapter_id}", participants=[identity.id]
        )

        async with (
            cell.run_as(identity, builder_kwargs={"cli_settings": str(settings)}),
            reply_capture(room_id) as capture,
        ):
            word = unique_marker("settings-turn")
            message_id = await user_ops.send_message(
                room_id,
                liveness_probe(word),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(message_id, identity.id)
            replies.assert_contains_any([word])

        assert hook_marker.is_file(), (
            "the selected settings file did not steer the real CLI during the live "
            "room turn: --settings was withheld from the launch, or the hook never ran"
        )
