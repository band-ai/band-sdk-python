"""Tests for the ``surface`` field on ``ToolDefinition`` and surface-aware
filter composition in ``iter_tool_definitions``.

These tests cover the Phase 1 (INT-349) acceptance criteria:

- ``ToolDefinition.surface`` field exists and defaults to ``"agent"``.
- ``iter_tool_definitions(surface="agent")`` returns exactly the pre-Phase-1
  set (set equality on ``.name``).
- ``iter_tool_definitions(surface="human")`` returns only human tools.
- The three filters (``surface``, ``include_memory``, ``include_contacts``)
  compose as independent predicates.
- Every ``ToolDefinition.method_name`` resolves on the class implied by
  its surface.
- ``build_thenvoi_mcp_tool_registrations()`` with no args registers the
  pre-Phase-1 agent-only tool-name set (snapshot guard for LocalMCPServer).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from thenvoi.runtime.mcp_server import (
    build_resolved_thenvoi_mcp_tool_registrations,
    build_thenvoi_mcp_tool_registrations,
)
from thenvoi.runtime.tools import (
    AgentTools,
    HumanTools,
    TOOL_DEFINITIONS,
    ToolDefinition,
    iter_tool_definitions,
)


# Pre-Phase-1 agent tool-name set (snapshot). Changing the agent surface
# belongs in a different ticket; if this list drifts, either the test or
# the SDK behavior is out of sync with the ticket.
PRE_PHASE1_AGENT_TOOLS: frozenset[str] = frozenset(
    {
        "thenvoi_send_message",
        "thenvoi_send_event",
        "thenvoi_add_participant",
        "thenvoi_remove_participant",
        "thenvoi_lookup_peers",
        "thenvoi_get_participants",
        "thenvoi_create_chatroom",
        "thenvoi_list_contacts",
        "thenvoi_add_contact",
        "thenvoi_remove_contact",
        "thenvoi_list_contact_requests",
        "thenvoi_respond_contact_request",
    }
)

PHASE1_HUMAN_TOOLS: frozenset[str] = frozenset(
    {
        "thenvoi_list_my_agents",
        "thenvoi_register_my_agent",
        "thenvoi_list_my_chats",
        "thenvoi_create_my_chat_room",
        "thenvoi_get_my_chat_room",
        "thenvoi_list_my_contacts",
        "thenvoi_create_contact_request",
        "thenvoi_list_received_contact_requests",
        "thenvoi_list_sent_contact_requests",
        "thenvoi_approve_contact_request",
        "thenvoi_reject_contact_request",
        "thenvoi_cancel_contact_request",
        "thenvoi_resolve_handle",
        "thenvoi_remove_my_contact",
        "thenvoi_list_my_chat_messages",
        "thenvoi_send_my_chat_message",
        "thenvoi_list_my_chat_participants",
        "thenvoi_add_my_chat_participant",
        "thenvoi_remove_my_chat_participant",
        "thenvoi_get_my_profile",
        "thenvoi_update_my_profile",
        "thenvoi_list_my_peers",
    }
)


class TestToolDefinitionSurfaceField:
    """The new ``surface`` field exists and has the right defaults."""

    def test_surface_defaults_to_agent(self) -> None:
        """Constructing a ToolDefinition without ``surface`` yields ``"agent"``."""

        class _Dummy(BaseModel):
            pass

        definition = ToolDefinition(
            name="thenvoi_dummy", input_model=_Dummy, method_name="dummy"
        )
        assert definition.surface == "agent"

    def test_all_registered_agent_tools_have_agent_surface(self) -> None:
        """Every entry whose name is in the pre-Phase-1 set carries surface=agent."""
        for name in PRE_PHASE1_AGENT_TOOLS:
            assert TOOL_DEFINITIONS[name].surface == "agent"

    def test_all_registered_human_tools_have_human_surface(self) -> None:
        """Every Phase 1 human entry is tagged surface=human."""
        for name in PHASE1_HUMAN_TOOLS:
            assert TOOL_DEFINITIONS[name].surface == "human"


class TestIterToolDefinitionsSurfaceFilter:
    """``iter_tool_definitions(surface=...)`` set-equality guarantees."""

    def test_surface_agent_matches_pre_phase1_set(self) -> None:
        """``surface="agent"`` with defaults returns exactly the pre-Phase-1 set."""
        names = {d.name for d in iter_tool_definitions(surface="agent")}
        assert names == PRE_PHASE1_AGENT_TOOLS

    def test_default_surface_is_agent(self) -> None:
        """No-arg ``iter_tool_definitions()`` returns only agent tools.

        Regression guard for C1: existing callers (``claude_sdk``,
        ``opencode``, ``acp`` client adapter) pipe the result straight
        into ``create_thenvoi_mcp_backend`` without re-filtering, so the
        default must not leak human tools into agent-shaped backends.
        """
        defs = iter_tool_definitions()
        names = {d.name for d in defs}
        assert names == PRE_PHASE1_AGENT_TOOLS
        for definition in defs:
            assert definition.surface == "agent"

    def test_surface_human_returns_only_human_tools(self) -> None:
        """``surface="human"`` with defaults returns only human tools."""
        defs = iter_tool_definitions(surface="human")
        names = {d.name for d in defs}
        assert names == PHASE1_HUMAN_TOOLS - {
            # Default include_memory=False drops human memory tools too.
            "thenvoi_list_user_memories",
            "thenvoi_get_user_memory",
            "thenvoi_supersede_user_memory",
            "thenvoi_archive_user_memory",
            "thenvoi_restore_user_memory",
            "thenvoi_delete_user_memory",
        }
        for definition in defs:
            assert definition.surface == "human"

    def test_surface_none_returns_union_of_both_surfaces(self) -> None:
        """``surface=None`` with defaults returns both surfaces' default views."""
        names = {d.name for d in iter_tool_definitions(surface=None)}
        # Default filters strip memory (agent + human) but keep contacts.
        agent = PRE_PHASE1_AGENT_TOOLS
        human_without_memory = PHASE1_HUMAN_TOOLS - {
            "thenvoi_list_user_memories",
            "thenvoi_get_user_memory",
            "thenvoi_supersede_user_memory",
            "thenvoi_archive_user_memory",
            "thenvoi_restore_user_memory",
            "thenvoi_delete_user_memory",
        }
        assert names == agent | human_without_memory


class TestIterToolDefinitionsFilterComposition:
    """The three filters (``surface``, ``include_memory``, ``include_contacts``)
    compose as independent predicates."""

    def test_human_memory_only(self) -> None:
        """``surface=human, include_memory=True, include_contacts=False`` returns
        only human memory tools."""
        names = {
            d.name
            for d in iter_tool_definitions(
                surface="human", include_memory=True, include_contacts=False
            )
        }
        expected_memory = {
            "thenvoi_list_user_memories",
            "thenvoi_get_user_memory",
            "thenvoi_supersede_user_memory",
            "thenvoi_archive_user_memory",
            "thenvoi_restore_user_memory",
            "thenvoi_delete_user_memory",
        }
        # No agent tools at all, no human contact tools.
        assert expected_memory <= names
        # Remaining human tools (baseline + profile + agents + peers + chats
        # + participants + messages) are still present because include_memory
        # and include_contacts apply independently of surface.
        # Specifically: no contact tools of either surface leak through.
        assert names.isdisjoint(
            {
                "thenvoi_list_my_contacts",
                "thenvoi_create_contact_request",
                "thenvoi_resolve_handle",
                "thenvoi_list_contacts",
                "thenvoi_add_contact",
            }
        )
        # No agent surface tools at all.
        assert names.isdisjoint(PRE_PHASE1_AGENT_TOOLS)

    def test_baseline_across_both_surfaces(self) -> None:
        """``surface=None, include_memory=False, include_contacts=False``
        returns baseline tools from both surfaces — no memory, no contacts."""
        names = {
            d.name
            for d in iter_tool_definitions(
                surface=None, include_memory=False, include_contacts=False
            )
        }
        # No contact tools of either surface.
        assert names.isdisjoint(
            {
                "thenvoi_list_contacts",
                "thenvoi_add_contact",
                "thenvoi_remove_contact",
                "thenvoi_list_contact_requests",
                "thenvoi_respond_contact_request",
                "thenvoi_list_my_contacts",
                "thenvoi_create_contact_request",
                "thenvoi_list_received_contact_requests",
                "thenvoi_list_sent_contact_requests",
                "thenvoi_approve_contact_request",
                "thenvoi_reject_contact_request",
                "thenvoi_cancel_contact_request",
                "thenvoi_resolve_handle",
                "thenvoi_remove_my_contact",
            }
        )
        # No memory tools of either surface.
        assert names.isdisjoint(
            {
                "thenvoi_list_memories",
                "thenvoi_store_memory",
                "thenvoi_get_memory",
                "thenvoi_supersede_memory",
                "thenvoi_archive_memory",
                "thenvoi_list_user_memories",
                "thenvoi_get_user_memory",
                "thenvoi_supersede_user_memory",
                "thenvoi_archive_user_memory",
                "thenvoi_restore_user_memory",
                "thenvoi_delete_user_memory",
            }
        )
        # But baseline agent + human tools remain.
        assert "thenvoi_send_message" in names
        assert "thenvoi_get_my_profile" in names
        assert "thenvoi_send_my_chat_message" in names

    def test_include_memory_true_agent_only(self) -> None:
        """``surface=agent, include_memory=True`` includes agent memory tools."""
        names = {
            d.name for d in iter_tool_definitions(surface="agent", include_memory=True)
        }
        agent_memory = {
            "thenvoi_list_memories",
            "thenvoi_store_memory",
            "thenvoi_get_memory",
            "thenvoi_supersede_memory",
            "thenvoi_archive_memory",
        }
        assert agent_memory <= names
        # No human memory tools.
        assert "thenvoi_list_user_memories" not in names


class TestMethodNameResolution:
    """Every ``ToolDefinition.method_name`` must resolve on the class
    implied by ``surface``. Every ``input_model`` must be a BaseModel subclass."""

    def test_every_input_model_is_basemodel_subclass(self) -> None:
        for name, definition in TOOL_DEFINITIONS.items():
            assert issubclass(definition.input_model, BaseModel), (
                f"{name} input_model is not a BaseModel subclass"
            )

    def test_every_agent_method_name_resolves_on_agenttools(self) -> None:
        for definition in iter_tool_definitions(
            surface="agent", include_memory=True, include_contacts=True
        ):
            assert hasattr(AgentTools, definition.method_name), (
                f"AgentTools has no method {definition.method_name} "
                f"(from {definition.name})"
            )

    def test_every_human_method_name_resolves_on_humantools(self) -> None:
        for definition in iter_tool_definitions(
            surface="human", include_memory=True, include_contacts=True
        ):
            assert hasattr(HumanTools, definition.method_name), (
                f"HumanTools has no method {definition.method_name} "
                f"(from {definition.name})"
            )


class TestLocalMCPServerAgentOnlySnapshot:
    """``LocalMCPServer`` must stay agent-only in Phase 1. The snapshot tests
    guard against a future change silently leaking human tools into either
    ``build_thenvoi_mcp_tool_registrations()`` or its resolved variant."""

    def test_build_thenvoi_mcp_tool_registrations_is_agent_only(self) -> None:
        """No-arg call registers exactly the pre-Phase-1 agent tool-name set."""
        agent_tools = MagicMock(spec=AgentTools)
        registrations = build_thenvoi_mcp_tool_registrations(agent_tools)
        names = {registration.name for registration in registrations}
        assert names == PRE_PHASE1_AGENT_TOOLS

    def test_build_resolved_thenvoi_mcp_tool_registrations_is_agent_only(
        self,
    ) -> None:
        """Resolved variant also registers only the pre-Phase-1 agent set."""

        def _resolver(_room_id: str):
            return None

        registrations = build_resolved_thenvoi_mcp_tool_registrations(
            get_tools=_resolver
        )
        names = {registration.name for registration in registrations}
        assert names == PRE_PHASE1_AGENT_TOOLS

    def test_build_registrations_filters_non_agent_definitions(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Defense-in-depth: if a caller passes a mixed ``tool_definitions``
        list, ``build_thenvoi_mcp_tool_registrations`` drops non-agent
        entries (they'd ``AttributeError`` on ``AgentTools`` at call time)
        and logs a warning per dropped entry.
        """
        import logging

        agent_tools = MagicMock(spec=AgentTools)
        mixed = [
            TOOL_DEFINITIONS["thenvoi_send_message"],
            TOOL_DEFINITIONS["thenvoi_send_my_chat_message"],
            TOOL_DEFINITIONS["thenvoi_get_my_profile"],
        ]

        with caplog.at_level(logging.WARNING, logger="thenvoi.runtime.mcp_server"):
            registrations = build_thenvoi_mcp_tool_registrations(
                agent_tools, tool_definitions=mixed
            )

        names = {r.name for r in registrations}
        assert names == {"thenvoi_send_message"}
        # One warning per dropped human entry.
        warnings = [
            record for record in caplog.records if record.levelno == logging.WARNING
        ]
        dropped_names = {
            "thenvoi_send_my_chat_message",
            "thenvoi_get_my_profile",
        }
        assert len(warnings) == len(dropped_names)
        for record in warnings:
            assert "non-agent tool definition" in record.getMessage()

    def test_build_resolved_registrations_filters_non_agent_definitions(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Resolved variant applies the same defense-in-depth filter. This
        is the path the opencode/claude_sdk/acp adapters exercise."""
        import logging

        def _resolver(_room_id: str):
            return None

        mixed = [
            TOOL_DEFINITIONS["thenvoi_send_message"],
            TOOL_DEFINITIONS["thenvoi_add_participant"],
            TOOL_DEFINITIONS["thenvoi_send_my_chat_message"],
        ]

        with caplog.at_level(logging.WARNING, logger="thenvoi.runtime.mcp_server"):
            registrations = build_resolved_thenvoi_mcp_tool_registrations(
                get_tools=_resolver, tool_definitions=mixed
            )

        names = {r.name for r in registrations}
        assert names == {"thenvoi_send_message", "thenvoi_add_participant"}
        warnings = [
            record for record in caplog.records if record.levelno == logging.WARNING
        ]
        assert len(warnings) == 1
        assert "thenvoi_send_my_chat_message" in warnings[0].getMessage()
