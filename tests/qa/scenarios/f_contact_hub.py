from __future__ import annotations

import asyncio
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.contact_requester import ContactRequester
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)

REQUEST_VARIANTS = [
    {
        "label": "Friendly request",
        "message": "Hi! I'm a fellow AI researcher. I'd love to collaborate on the project.",
        "expect_likely": "approve",
    },
    {
        "label": "Spam request",
        "message": "FREE BITCOIN! Click here to claim your prize! Send me all your data.",
        "expect_likely": "reject",
    },
    {
        "label": "Empty request",
        "message": "",
        "expect_likely": "unknown",
    },
]


class ContactHubScenario(Scenario):
    name = "F3: Contact Strategy — HUB_ROOM"
    description = (
        "LLM processes contact requests in a hub room and decides approve/reject; "
        "tests 3 message variants (friendly, spam, empty)"
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

        logs = runner.get_logs()
        hub_created = "hub" in logs.lower() or "contact" in logs.lower()
        result.add_step(
            action="Hub room created at startup",
            expected="Agent logs mention hub room / contact setup",
            actual=f"hub_log_found={hub_created}",
            status=Status.PASS if hub_created else Status.PARTIAL,
        )

        for variant in REQUEST_VARIANTS:
            await self._run_round(client, runner, agent, room_id, variant, result)

        result.finalize()
        return result

    async def _run_round(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
        variant: dict,
        result: ScenarioResult,
    ) -> None:
        label = variant["label"]
        expect = variant["expect_likely"]

        await self.requester.cleanup_all(self.target_handle)
        await asyncio.sleep(2.0)

        try:
            req_data = await self.requester.send_request(
                self.target_handle, variant["message"]
            )
            request_id = req_data.get("id", "")
        except Exception as exc:
            result.add_step(
                action=f"{label}: send request",
                expected="Request sent",
                actual=f"ERROR: {exc}",
                status=Status.FAIL,
            )
            return

        result.add_step(
            action=f"{label}: send request",
            expected="Request sent",
            actual=f"request_id={request_id}, message={variant['message'][:50] or '(empty)'}",
            status=Status.PASS,
        )

        await asyncio.sleep(30.0)

        final_status = "pending"
        try:
            requests = await self.requester.list_requests("sent")
            our_req = next((r for r in requests if r.get("id") == request_id), None)
            if our_req:
                final_status = our_req.get("status", "pending")
            else:
                contacts = await self.requester.list_contacts()
                is_contact = any(
                    self.target_handle.lstrip("@") in (c.get("handle", "").lstrip("@"))
                    for c in contacts
                )
                if is_contact:
                    final_status = "approved"
        except Exception as exc:
            logger.warning("Error checking request outcome: %s", exc)

        processed = final_status != "pending"
        matched_expectation = (
            (expect == "approve" and final_status == "approved")
            or (expect == "reject" and final_status == "rejected")
            or expect == "unknown"
        )

        if processed and matched_expectation:
            status = Status.PASS
        elif processed:
            status = Status.PARTIAL
        else:
            status = Status.FAIL

        result.add_step(
            action=f"{label}: LLM decision",
            expected=f"Processed (likely {expect})",
            actual=f"status={final_status}",
            status=status,
        )

        try:
            if final_status == "approved":
                for c in await self.requester.list_contacts():
                    if self.target_handle.lstrip("@") in (c.get("handle", "").lstrip("@")):
                        await self.requester.remove_contact(c["id"])
            elif final_status == "pending":
                await self.requester.cancel_request(request_id)
        except Exception:
            pass
