"""A host's Claude settings file acts during a real room turn.

Through the full ``Agent`` stack, the real CLI runs the selected file's SessionStart
hook while the room still gets its Band reply, so composing the host's settings with
the adapter's isolation keys cannot have cost the Band tools.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from band import create_room_workspace_resolver
from band.adapters.claude_sdk import (
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import (
    REPLY_PROMPT,
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import ResourceManager, running_agent
from tests.e2e.baseline.toolkit.user_ops import UserOps

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=1)


def _marker_hook_settings(marker: Path) -> dict[str, object]:
    """Settings whose SessionStart hook creates ``marker``; POSIX paths and this
    interpreter keep the command valid under the CLI's shell on every OS."""
    touch = (
        f'"{Path(sys.executable).as_posix()}" -c '
        f"\"import pathlib; pathlib.Path('{marker.as_posix()}').touch()\""
    )
    return {
        "hooks": {
            "SessionStart": [
                {"matcher": "startup", "hooks": [{"type": "command", "command": touch}]}
            ]
        }
    }


class TestClaudeSdkSettings:
    @lane(Lane.CORE)
    @requires(Dep.ANTHROPIC)
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_settings_file_acts_while_the_room_gets_its_reply(
        self,
        baseline_settings: BaselineSettings,
        resource_manager: ResourceManager,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
        tmp_path: Path,
    ) -> None:
        marker = tmp_path / "session-start-marker"
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps(_marker_hook_settings(marker)))
        adapter = ClaudeSDKAdapter(
            ClaudeSDKAdapterConfig(
                model=baseline_settings.llm_models.anthropic_model,
                custom_section=REPLY_PROMPT,
                cli=ClaudeCLIOptions(settings=str(settings)),
            ),
            workspace_for_room=create_room_workspace_resolver(tmp_path / "rooms"),
        )
        identity = await resource_manager.provision_agent("claude-settings")
        room_id = await resource_manager.provision_room(
            title="e2e-claude-settings", participants=[identity.id]
        )

        async with (
            running_agent(identity, adapter, baseline_settings),
            reply_capture(room_id) as capture,
        ):
            word = unique_marker("settings")
            message_id = await user_ops.send_message(
                room_id,
                liveness_probe(word),
                mention_id=identity.id,
                mention_name=identity.name,
            )
            replies = await capture.wait_for_reply(message_id, identity.id)

        replies.assert_contains_any([word])
        assert marker.is_file(), "the real CLI never ran the settings file's hook"
