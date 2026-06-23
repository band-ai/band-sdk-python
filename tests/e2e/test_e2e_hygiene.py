from __future__ import annotations

import importlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tests.e2e.conftest import (
    DEFAULT_E2E_ADAPTERS,
    E2ESettings,
    _PROVIDER_BASE_URL_ENV_VARS,
    _RateLimitedObjectProxy,
    _assert_room_creation_budget_available,
    _cleared_provider_base_url_env_vars,
    _created_room_budget_from_env,
    _track_created_room,
)
from tests.e2e.helpers import (
    TrackingWebSocketClient,
    assert_required_tool_observations,
    listening_for_agent_responses,
    require_successful_tool_execution,
    tool_observations_after_boundary,
)


def test_e2e_settings_reject_placeholder_openai_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_LLM_MODEL", "gpt-X.X-mini")

    with pytest.raises(ValidationError, match="concrete model name"):
        E2ESettings()


def test_e2e_settings_reject_placeholder_anthropic_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_ANTHROPIC_MODEL", "claude-placeholder")

    with pytest.raises(ValidationError, match="concrete model name"):
        E2ESettings()


def test_default_e2e_adapter_matrix_excludes_crewai_lane() -> None:
    assert "crewai" not in DEFAULT_E2E_ADAPTERS
    assert "pydantic_ai" in DEFAULT_E2E_ADAPTERS
    assert "codex" in DEFAULT_E2E_ADAPTERS


def _non_bridge_adapter_modules() -> set[str]:
    """Every adapter module on disk, minus protocol bridges.

    Derived from ``src/band/adapters/*.py`` so a newly added adapter is
    automatically required in the matrices below — the coverage check fails
    closed instead of silently missing it.
    """
    import band.adapters
    from tests.framework_conformance.injection_registry import (
        INJECTION_EXCLUDED_MODULES,
    )

    adapters_dir = Path(band.adapters.__file__).parent
    modules = {
        path.stem for path in adapters_dir.glob("*.py") if path.stem != "__init__"
    }
    return modules - set(INJECTION_EXCLUDED_MODULES)


def test_baseline_l0_adapter_matrix_covers_every_non_bridge_adapter() -> None:
    from tests.e2e.adapters.conftest import (
        BASELINE_L0_ADAPTER_FACTORIES,
        BASELINE_L0_BLOCKED_ADAPTER_NAMES,
    )

    expected_non_bridge = _non_bridge_adapter_modules()
    runnable = set(BASELINE_L0_ADAPTER_FACTORIES)
    blocked = set(BASELINE_L0_BLOCKED_ADAPTER_NAMES)

    covered = runnable | blocked
    assert covered == expected_non_bridge, {
        "uncovered_new_adapters": sorted(expected_non_bridge - covered),
        "stale_matrix_entries": sorted(covered - expected_non_bridge),
    }
    assert not (runnable & blocked)


def test_provider_usage_baseline_matrix_covers_every_non_bridge_adapter() -> None:
    from tests.e2e.adapters.conftest import (
        BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES,
        BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES,
    )

    expected_non_bridge = {
        "anthropic",
        "claude_sdk",
        "codex",
        "crewai",
        "crewai_flow",
        "gemini",
        "google_adk",
        "langgraph",
        "letta",
        "opencode",
        "parlant",
        "pydantic_ai",
    }
    runnable = set(BASELINE_DEFAULT_PROVIDER_USAGE_ADAPTER_FACTORIES)
    blocked = set(BASELINE_DEFAULT_PROVIDER_USAGE_BLOCKED_ADAPTER_NAMES)

    assert runnable | blocked == expected_non_bridge
    assert not (runnable & blocked)


def test_provider_base_url_overrides_are_scoped_for_live_e2e(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in _PROVIDER_BASE_URL_ENV_VARS:
        monkeypatch.setenv(name, "http://localhost:9999")

    with _cleared_provider_base_url_env_vars():
        for name in _PROVIDER_BASE_URL_ENV_VARS:
            assert name not in os.environ

    for name in _PROVIDER_BASE_URL_ENV_VARS:
        assert os.environ[name] == "http://localhost:9999"


async def test_rate_limited_rest_proxy_waits_before_nested_api_calls() -> None:
    endpoint = SimpleNamespace(fetch=AsyncMock(return_value="ok"))
    limiter = SimpleNamespace(wait=AsyncMock())
    proxy = _RateLimitedObjectProxy(SimpleNamespace(endpoint=endpoint), limiter)

    result = await proxy.endpoint.fetch("room-1")

    assert result == "ok"
    limiter.wait.assert_awaited_once_with()
    endpoint.fetch.assert_awaited_once_with("room-1")


def test_crewai_factory_skips_without_crewai_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.e2e.adapters import conftest as adapter_conftest

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(adapter_conftest, "_is_conflicting_crewai_lane", lambda: True)

    with pytest.raises(pytest.skip.Exception, match="dev-crewai lane"):
        adapter_conftest.create_crewai_adapter(E2ESettings())


def test_parlant_module_import_does_not_mutate_agent_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAND_API_KEY", "thnv_u_test_user_key")
    monkeypatch.delenv("TEST_AGENT_ID", raising=False)
    monkeypatch.delenv("BAND_AGENT_ID", raising=False)
    monkeypatch.delenv("BAND_API_KEY_USER", raising=False)

    module = importlib.import_module("tests.e2e.adapters.test_parlant")
    importlib.reload(module)

    assert "TEST_AGENT_ID" not in os.environ
    assert "BAND_AGENT_ID" not in os.environ
    assert "BAND_API_KEY_USER" not in os.environ
    assert os.environ["BAND_API_KEY"] == "thnv_u_test_user_key"


def test_room_creation_budget_rejects_before_create() -> None:
    with pytest.raises(pytest.fail.Exception, match="budget exhausted"):
        _assert_room_creation_budget_available(
            created_room_ids=["room-1"],
            budget=1,
            label="adapter:fresh",
        )


async def test_l3_room_creation_rejects_before_create_when_budget_exhausted() -> None:
    from tests.e2e.scenarios.test_baseline_l3_multiparty import (
        _LiveAgentSpec,
        _create_l3_room,
    )

    client = SimpleNamespace(
        agent_api_chats=SimpleNamespace(create_agent_chat=AsyncMock()),
        agent_api_participants=SimpleNamespace(add_agent_chat_participant=AsyncMock()),
    )
    spec = _LiveAgentSpec(
        role="test",
        agent_id="agent-1",
        api_key="thnv_a_test",
        name="Agent One",
        handle="owner/agent-one",
        description="Agent one description",
    )

    with pytest.raises(pytest.fail.Exception, match="budget exhausted"):
        await _create_l3_room(
            client,
            spec,
            spec,
            spec,
            created_room_ids=["room-1"],
            room_creation_budget=1,
            user_peer=SimpleNamespace(id="user-1"),
        )

    client.agent_api_chats.create_agent_chat.assert_not_awaited()
    client.agent_api_participants.add_agent_chat_participant.assert_not_awaited()


def test_created_room_tracking_appends_only_with_budget() -> None:
    created_room_ids: list[str] = []

    _track_created_room(
        created_room_ids=created_room_ids,
        budget=1,
        room_id="room-1",
        label="adapter:fresh",
    )

    assert created_room_ids == ["room-1"]
    with pytest.raises(pytest.fail.Exception, match="budget exhausted"):
        _track_created_room(
            created_room_ids=created_room_ids,
            budget=1,
            room_id="room-2",
            label="adapter:fresh-2",
        )
    assert created_room_ids == ["room-1"]


def test_room_creation_budget_env_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_CREATED_ROOM_BUDGET", "2")
    assert _created_room_budget_from_env() == 2

    monkeypatch.setenv("E2E_CREATED_ROOM_BUDGET", "-1")
    with pytest.raises(ValueError, match=">= 0"):
        _created_room_budget_from_env()


def test_codex_e2e_requires_explicit_disposable_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.e2e.adapters import conftest as adapter_conftest
    from tests.e2e.settings_groups import CodexSettings

    monkeypatch.delenv("CODEX_CWD", raising=False)
    with pytest.raises(pytest.skip.Exception, match="CODEX_CWD"):
        adapter_conftest._require_codex_disposable_cwd(CodexSettings())

    monkeypatch.setenv("CODEX_CWD", str(tmp_path))
    monkeypatch.delenv("E2E_CODEX_CWD_IS_DISPOSABLE", raising=False)
    with pytest.raises(pytest.skip.Exception, match="E2E_CODEX_CWD_IS_DISPOSABLE"):
        adapter_conftest._require_codex_disposable_cwd(CodexSettings())

    monkeypatch.setenv("E2E_CODEX_CWD_IS_DISPOSABLE", "true")
    assert adapter_conftest._require_codex_disposable_cwd(CodexSettings()) == str(
        tmp_path.resolve()
    )


def test_write_capable_auto_approval_requires_opt_in() -> None:
    from tests.e2e.adapters import conftest as adapter_conftest

    # auto_accept is refused unless the write-capable opt-in is set.
    with pytest.raises(pytest.skip.Exception, match="auto_accept requires"):
        adapter_conftest._safe_approval_mode(
            adapter_name="Codex", mode="auto_accept", opted_in=False
        )

    # auto_accept is allowed once opted in.
    assert (
        adapter_conftest._safe_approval_mode(
            adapter_name="Codex", mode="auto_accept", opted_in=True
        )
        == "auto_accept"
    )

    # Non-write-capable modes pass through regardless of opt-in.
    assert (
        adapter_conftest._safe_approval_mode(
            adapter_name="Codex", mode="manual", opted_in=False
        )
        == "manual"
    )


class _FailingLeaveWebSocket:
    async def join_chat_room_channel(
        self,
        chat_room_id: str,
        on_message_created: Callable[[Any], Awaitable[None]],
    ) -> None:
        self.chat_room_id = chat_room_id
        self.on_message_created = on_message_created

    async def leave_chat_room_channel(self, chat_room_id: str) -> None:
        raise RuntimeError(f"cannot leave {chat_room_id}")


async def test_tracking_websocket_cleanup_failures_are_visible() -> None:
    ws = TrackingWebSocketClient(_FailingLeaveWebSocket())
    await ws.join_chat_room_channel("room-1", AsyncMock())

    with pytest.raises(AssertionError, match="Failed to leave E2E WebSocket"):
        await ws.cleanup_channels()


async def test_listener_cleanup_failure_fails_when_body_succeeds() -> None:
    ws = _FailingLeaveWebSocket()

    with pytest.raises(AssertionError, match="Failed to leave E2E WebSocket"):
        async with listening_for_agent_responses(ws, "room-1"):
            pass


async def test_listener_cleanup_failure_preserves_primary_failure() -> None:
    ws = _FailingLeaveWebSocket()

    with pytest.raises(ValueError, match="primary") as exc_info:
        async with listening_for_agent_responses(ws, "room-1"):
            raise ValueError("primary")

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("Cleanup error" in note for note in notes)


def _fake_message(
    message_id: str,
    message_type: str,
    *,
    sender_id: str = "agent-1",
    content: str = "",
    room_id: str = "room-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        message_type=message_type,
        sender_id=sender_id,
        chat_room_id=room_id,
        content=content,
        metadata={},
    )


def test_tool_observations_require_call_and_result_after_turn_boundary() -> None:
    messages = [
        _fake_message("trigger-1", "text", sender_id="user-1"),
        _fake_message(
            "event-1",
            "tool_call",
            content=(
                '{"name":"band_get_participants","tool_call_id":"call-1","args":{}}'
            ),
        ),
        _fake_message(
            "event-2",
            "tool_result",
            content=(
                '{"name":"band_get_participants","tool_call_id":"call-1","output":[]}'
            ),
        ),
    ]

    observations = tool_observations_after_boundary(
        messages,
        room_id="room-1",
        agent_id="agent-1",
        after_message_id="trigger-1",
    )

    assert [observation.message_type for observation in observations] == [
        "tool_call",
        "tool_result",
    ]
    assert_required_tool_observations(
        observations,
        required_tool_names={"band_get_participants"},
    )


def test_tool_observations_ignore_correct_text_without_read_tool_events() -> None:
    messages = [
        _fake_message("trigger-1", "text", sender_id="user-1"),
        _fake_message(
            "reply-1",
            "text",
            content="CURRENT_ROOM: User, Agent\nINVITABLE_PEERS: Echo",
        ),
    ]

    observations = tool_observations_after_boundary(
        messages,
        room_id="room-1",
        agent_id="agent-1",
        after_message_id="trigger-1",
    )

    with pytest.raises(AssertionError, match="band_get_participants"):
        assert_required_tool_observations(
            observations,
            required_tool_names={
                "band_get_participants",
                "band_lookup_peers",
            },
        )


def test_tool_observations_do_not_cross_turn_boundary() -> None:
    messages = [
        _fake_message(
            "event-before",
            "tool_call",
            content=('{"name":"band_remove_participant","tool_call_id":"old-call"}'),
        ),
        _fake_message("trigger-1", "text", sender_id="user-1"),
    ]

    observations = tool_observations_after_boundary(
        messages,
        room_id="room-1",
        agent_id="agent-1",
        after_message_id="trigger-1",
    )

    assert observations == []
    with pytest.raises(AssertionError, match="band_remove_participant"):
        assert_required_tool_observations(
            observations,
            required_tool_names={"band_remove_participant"},
        )


def test_successful_tool_execution_correlates_unnamed_result_by_call_id() -> None:
    messages = [
        _fake_message("trigger-1", "text", sender_id="user-1"),
        _fake_message(
            "event-1",
            "tool_call",
            content=(
                '{"name":"band_send_message",'
                '"tool_call_id":"call-1","args":{"content":"hello"}}'
            ),
        ),
        _fake_message(
            "event-2",
            "tool_result",
            content='{"tool_call_id":"call-1","output":{"id":"msg-1"}}',
        ),
    ]

    observations = tool_observations_after_boundary(
        messages,
        room_id="room-1",
        agent_id="agent-1",
        after_message_id="trigger-1",
    )
    execution = require_successful_tool_execution(
        observations,
        tool_name="band_send_message",
    )

    assert execution.tool_call_id == "call-1"
    assert execution.call.event_id == "event-1"
    assert execution.result.event_id == "event-2"


def test_successful_tool_execution_rejects_error_result() -> None:
    messages = [
        _fake_message("trigger-1", "text", sender_id="user-1"),
        _fake_message(
            "event-1",
            "tool_call",
            content=(
                '{"name":"band_send_message",'
                '"tool_call_id":"call-1","args":{"content":"hello"}}'
            ),
        ),
        _fake_message(
            "event-2",
            "tool_result",
            content=(
                '{"name":"band_send_message",'
                '"tool_call_id":"call-1","output":"Error executing band_send_message"}'
            ),
        ),
    ]

    observations = tool_observations_after_boundary(
        messages,
        room_id="room-1",
        agent_id="agent-1",
        after_message_id="trigger-1",
    )

    with pytest.raises(AssertionError, match="reported an error"):
        require_successful_tool_execution(
            observations,
            tool_name="band_send_message",
        )
