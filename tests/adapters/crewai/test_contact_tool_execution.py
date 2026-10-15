from __future__ import annotations

import asyncio
import json
from typing import Any

from band.core.types import Capability


class TestContactToolExecution:
    def _make_adapter(self, CrewAIAdapter: type) -> Any:
        return CrewAIAdapter(
            capabilities=Capability.CONTACTS,
        )

    def test_list_contacts_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        list_contacts_tool = next(t for t in tools if t.name == "band_list_contacts")

        with room_context("room-123"):
            result = list_contacts_tool._run(page=2, page_size=25)

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["data"][0]["handle"] == "@alice"
        mock_tools.list_contacts.assert_awaited_once_with(2, 25)

    def test_add_contact_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        add_contact_tool = next(t for t in tools if t.name == "band_add_contact")

        with room_context("room-123"):
            result = add_contact_tool._run(handle="@alice", message="Hi Alice")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "pending"
        assert result_data["handle"] == "@alice"
        mock_tools.add_contact.assert_awaited_once_with("@alice", "Hi Alice")

    def test_remove_contact_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        remove_contact_tool = next(t for t in tools if t.name == "band_remove_contact")

        with room_context("room-123"):
            result = remove_contact_tool._run(handle="@alice", contact_id="contact-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "removed"
        mock_tools.remove_contact.assert_awaited_once_with("@alice", "contact-1")

    def test_list_contact_requests_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        list_requests_tool = next(
            t for t in tools if t.name == "band_list_contact_requests"
        )

        with room_context("room-123"):
            result = list_requests_tool._run(
                page=3, page_size=10, sent_status="approved"
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["data"]["received"][0]["id"] == "request-1"
        mock_tools.list_contact_requests.assert_awaited_once_with(3, 10, "approved")

    def test_respond_contact_request_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = self._make_adapter(CrewAIAdapter)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        respond_request_tool = next(
            t for t in tools if t.name == "band_respond_contact_request"
        )

        with room_context("room-123"):
            result = respond_request_tool._run(
                action="approve",
                handle="@alice",
                request_id="request-1",
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "approved"
        assert result_data["id"] == "request-1"
        mock_tools.respond_contact_request.assert_awaited_once_with(
            "approve",
            "@alice",
            "request-1",
        )
