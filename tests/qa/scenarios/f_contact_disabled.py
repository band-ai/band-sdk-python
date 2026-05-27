from __future__ import annotations

import asyncio
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.contact_requester import ContactRequester
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)


class ContactDisabledScenario(Scenario):
    name = "F1: Contact Strategy — DISABLED"
    description = "Contact requests are completely ignored when strategy is DISABLED"

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
                self.target_handle, "Hello from DISABLED test"
            )
            request_id = req_data.get("id", "")
            result.add_step(
                action="Send contact request",
                expected="Request sent successfully",
                actual=f"request_id={request_id}",
                status=Status.PASS if request_id else Status.FAIL,
            )
        except Exception as exc:
            result.mark_fail(f"Failed to send contact request: {exc}")
            return result

        await asyncio.sleep(10.0)

        try:
            requests = await self.requester.list_requests("sent")
            our_req = next(
                (r for r in requests if r.get("id") == request_id), None
            )
            still_pending = our_req and our_req.get("status") == "pending"
            result.add_step(
                action="Check request still pending",
                expected="Status remains 'pending' (DISABLED ignores events)",
                actual=f"status={our_req.get('status') if our_req else 'NOT FOUND'}",
                status=Status.PASS if still_pending else Status.FAIL,
            )
        except Exception as exc:
            result.add_step(
                action="Check request status",
                expected="pending",
                actual=f"ERROR: {exc}",
                status=Status.FAIL,
            )

        messages = await client.get_messages(room_id)
        contact_msgs = [
            m for m in messages
            if m.get("sender_id") == agent.agent_id
            and "contact" in (m.get("content") or "").lower()
        ]
        result.add_step(
            action="No contact messages in room",
            expected="Zero contact-related agent messages",
            actual=f"{len(contact_msgs)} contact message(s)",
            status=Status.PASS if len(contact_msgs) == 0 else Status.FAIL,
        )

        try:
            await self.requester.cancel_request(request_id)
        except Exception:
            pass

        result.finalize()
        return result
