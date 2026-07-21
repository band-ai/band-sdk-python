"""In-memory platform stand-in and observable tool surface for baseline tests."""

from __future__ import annotations

from typing import Any

from band.runtime.tools import serialize_tool_result
from band.testing.fake_tools import FakeAgentTools


class BaselineTools(FakeAgentTools):
    """Stateful fake tools that dispatch injected calls through platform methods.

    Adapters receive this object exactly as they receive runtime tools: they ask
    it for their native schemas, then execute the chosen tool by name.  The
    state and call log are the baseline observation surface.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.schema_requests: list[dict[str, Any]] = []
        self.contact_requests: list[dict[str, Any]] = []

    def get_anthropic_tool_schemas(
        self, *, include_memory: bool = False, include_contacts: bool = True
    ) -> list[dict[str, Any]]:
        self.schema_requests.append(
            {
                "format": "anthropic",
                "include_memory": include_memory,
                "include_contacts": include_contacts,
            }
        )
        names = [
            "band_send_message",
            "band_send_event",
            "band_add_participant",
            "band_remove_participant",
            "band_get_participants",
            "band_lookup_peers",
            "band_create_chatroom",
        ]
        if include_contacts:
            names.extend(
                [
                    "band_list_contacts",
                    "band_add_contact",
                    "band_remove_contact",
                    "band_list_contact_requests",
                    "band_respond_contact_request",
                ]
            )
        if include_memory:
            names.extend(
                [
                    "band_list_memories",
                    "band_store_memory",
                    "band_get_memory",
                    "band_supersede_memory",
                    "band_archive_memory",
                ]
            )
        return [
            {
                "name": name,
                "description": f"Observable baseline stub for {name}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for name in names
        ]

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Record and dispatch a platform call using the real tool method shape."""
        if not isinstance(arguments, dict):
            raise ValueError(f"{tool_name} arguments must be an object")
        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        return serialize_tool_result(await self._dispatch(tool_name, arguments))

    async def _dispatch(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        match tool_name:
            case "band_send_message":
                content = self._required_text(tool_name, arguments, "content")
                mentions = arguments.get("mentions", [])
                if not isinstance(mentions, list):
                    raise ValueError("band_send_message.mentions must be a list")
                return await self.send_message(content, mentions=mentions)
            case "band_send_event":
                return await self.send_event(
                    self._required_text(tool_name, arguments, "content"),
                    self._required_text(tool_name, arguments, "message_type"),
                    metadata=arguments.get("metadata"),
                )
            case "band_add_participant":
                return await self.add_participant(
                    self._required_text(tool_name, arguments, "identifier"),
                    role=arguments.get("role", "member"),
                )
            case "band_remove_participant":
                return await self.remove_participant(
                    self._required_text(tool_name, arguments, "identifier")
                )
            case "band_get_participants":
                return await self.get_participants()
            case "band_lookup_peers":
                return await self.lookup_peers(
                    page=arguments.get("page", 1),
                    page_size=arguments.get("page_size", 50),
                )
            case "band_create_chatroom":
                return await self.create_chatroom(task_id=arguments.get("task_id"))
            case "band_list_contacts":
                return await self.list_contacts(
                    page=arguments.get("page", 1),
                    page_size=arguments.get("page_size", 50),
                )
            case "band_add_contact":
                return await self.add_contact(
                    self._required_text(tool_name, arguments, "handle"),
                    message=arguments.get("message"),
                )
            case "band_remove_contact":
                return await self.remove_contact(
                    handle=arguments.get("handle"),
                    contact_id=arguments.get("contact_id"),
                )
            case "band_list_contact_requests":
                return await self.list_contact_requests(
                    page=arguments.get("page", 1),
                    page_size=arguments.get("page_size", 50),
                    sent_status=arguments.get("sent_status", "pending"),
                )
            case "band_respond_contact_request":
                action = self._required_text(tool_name, arguments, "action")
                result = await self.respond_contact_request(
                    action,
                    handle=arguments.get("handle"),
                    request_id=arguments.get("request_id"),
                )
                self.contact_requests.append({"action": action, "result": result})
                return result
            case "band_list_memories":
                return await self.list_memories(
                    page_size=arguments.get("page_size", 50)
                )
            case "band_store_memory":
                return await self.store_memory(
                    self._required_text(tool_name, arguments, "content"),
                    self._required_text(tool_name, arguments, "system"),
                    self._required_text(tool_name, arguments, "type"),
                    self._required_text(tool_name, arguments, "segment"),
                    self._required_text(tool_name, arguments, "thought"),
                    self._required_text(tool_name, arguments, "scope"),
                    subject_id=arguments.get("subject_id"),
                    metadata=arguments.get("metadata"),
                )
            case "band_get_memory":
                memory_id = self._required_text(tool_name, arguments, "memory_id")
                return next(
                    (memory for memory in self.memories if memory["id"] == memory_id),
                    None,
                )
            case "band_supersede_memory" | "band_archive_memory":
                memory_id = self._required_text(tool_name, arguments, "memory_id")
                status = (
                    "superseded" if tool_name == "band_supersede_memory" else "archived"
                )
                for memory in self.memories:
                    if memory["id"] == memory_id:
                        memory["status"] = status
                        return memory
                raise ValueError(f"Unknown memory: {memory_id}")
            case _:
                raise ValueError(f"Unknown platform tool: {tool_name}")

    @staticmethod
    def _required_text(tool_name: str, arguments: dict[str, Any], field: str) -> str:
        value = arguments.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{tool_name}.{field} must be a non-empty string")
        return value
