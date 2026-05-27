from __future__ import annotations

import asyncio
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.contact_requester import ContactRequester
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)


class ContactCallbackScenario(Scenario):
    name = "F2: Contact Strategy — CALLBACK"
    description = (
        "Callback auto-approves whitelisted handles (adk-qa-*) and rejects others; "
        "broadcast_changes=True produces a room notification"
    )

    def __init__(self, requester: ContactRequester, target_handle: str) -> None:
        self.requester = requester
        self.target_handle = target_handle

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)

        await self.requester.cleanup_all(self.target_handle)
        await asyncio.sleep(1.0)

        try:
            req_data = await self.requester.send_request(
                self.target_handle, "Collaboration request from QA"
            )
            request_id = req_data.get("id", "")
            result.add_step(
                action="Send whitelisted contact request",
                expected="Request sent",
                actual=f"request_id={request_id}",
                status=Status.PASS if request_id else Status.FAIL,
            )
        except Exception as exc:
            result.mark_fail(f"Failed to send request: {exc}")
            return result

        await asyncio.sleep(15.0)

        approved = False
        try:
            contacts = await self.requester.list_contacts()
            approved = any(
                self.target_handle.lstrip("@") in (c.get("handle", "").lstrip("@"))
                for c in contacts
            )
            if not approved:
                requests = await self.requester.list_requests("sent")
                our_req = next((r for r in requests if r.get("id") == request_id), None)
                approved = our_req is not None and our_req.get("status") == "approved"
        except Exception as exc:
            logger.warning("Error checking approval: %s", exc)

        result.add_step(
            action="Whitelisted handle auto-approved",
            expected="Request approved by callback (handle matches whitelist)",
            actual=f"approved={approved}",
            status=Status.PASS if approved else Status.FAIL,
        )

        messages = await client.get_messages(room_id)
        broadcast = any(
            "[contact" in (m.get("content") or "").lower()
            or "is now a contact" in (m.get("content") or "").lower()
            for m in messages
        )
        result.add_step(
            action="Broadcast notification in room",
            expected="broadcast_changes=True produces [Contacts] message",
            actual=f"broadcast_found={broadcast}",
            status=Status.PASS if broadcast else Status.PARTIAL,
        )

        tool_call_msgs = [
            m for m in messages if m.get("message_type") == "tool_call"
        ]
        result.add_step(
            action="No LLM invocation for callback",
            expected="Zero tool_call events (callback is programmatic)",
            actual=f"{len(tool_call_msgs)} tool_call event(s)",
            status=Status.PASS if len(tool_call_msgs) == 0 else Status.PARTIAL,
        )

        try:
            for c in await self.requester.list_contacts():
                if self.target_handle.lstrip("@") in (c.get("handle", "").lstrip("@")):
                    await self.requester.remove_contact(c["id"])
        except Exception:
            pass

        result.finalize()
        return result
