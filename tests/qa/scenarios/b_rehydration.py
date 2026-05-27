from __future__ import annotations

import asyncio
import logging
import random

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)

_SECRET_WORDS = [
    "pineapple", "armadillo", "kaleidoscope", "xylophone",
    "trampoline", "zeppelin", "chrysanthemum", "platypus",
]


def _new_agent_msgs(
    messages: list[dict],
    agent_id: str,
    known_ids: set[str],
    *,
    text_only: bool = False,
) -> list[dict]:
    return [
        m
        for m in messages
        if m.get("sender_id") == agent_id
        and m.get("sender_type") == "Agent"
        and m["id"] not in known_ids
        and (not text_only or m.get("message_type") == "text")
    ]


class RehydrationScenario(Scenario):
    name = "B: Agent Rehydration"
    description = (
        "Kill and restart the agent, verify it re-joins and responds in the same room"
    )

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
        shared: dict | None = None,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)
        secret = random.choice(_SECRET_WORDS)
        if shared is not None:
            shared["b_secret"] = secret

        known_ids: set[str] = set()
        messages = await client.get_messages(room_id)
        known_ids.update(m["id"] for m in messages)

        try:
            send_resp = await client.send_message(
                room_id,
                f"Before we restart: remember the word '{secret}'. I'll ask about it after.",
                agent,
            )
            if "id" in send_resp:
                known_ids.add(send_resp["id"])

            response = await client.wait_for_agent_activity(
                room_id,
                agent.agent_id,
                known_ids,
                timeout=120.0,
                settle_time=8.0,
            )
            if response:
                known_ids.add(response["id"])
                result.add_step(
                    action="Pre-restart message",
                    expected="Agent acknowledges",
                    actual=response.get("content", "")[:200],
                    status=Status.PASS,
                )
            else:
                result.add_step(
                    action="Pre-restart message",
                    expected="Agent acknowledges",
                    actual="NO RESPONSE",
                    status=Status.FAIL,
                )
        except Exception as e:
            result.add_step(
                action="Pre-restart message",
                expected="Agent acknowledges",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )

        # Snapshot unanswered user messages before the kill.
        # Scenario A may leave in-flight responses (e.g. slow lookup_peers)
        # that arrive after the kill — those are legitimate, not unsolicited.
        pre_kill_msgs = await client.get_messages(room_id, page_size=100)
        user_msgs_before_kill = sum(
            1 for m in pre_kill_msgs
            if m.get("sender_type") != "Agent"
            and m.get("message_type") == "text"
        )
        agent_replies_before_kill = sum(
            1 for m in pre_kill_msgs
            if m.get("sender_id") == agent.agent_id
            and m.get("sender_type") == "Agent"
            and m.get("message_type") == "text"
        )
        unanswered_before_kill = max(
            0, user_msgs_before_kill - agent_replies_before_kill
        )

        # B2: Stop agent
        graceful = await runner.stop()
        result.add_step(
            action="Stop agent (SIGINT)",
            expected="Graceful shutdown",
            actual=f"graceful={graceful}",
            status=Status.PASS if graceful else Status.PARTIAL,
        )

        await asyncio.sleep(2.0)

        # B3: Send recall question while agent is down
        try:
            send_resp = await client.send_message(
                room_id,
                "What word did I ask you to remember earlier?",
                agent,
            )
            if "id" in send_resp:
                known_ids.add(send_resp["id"])
        except Exception as e:
            logger.warning("Failed to send message while agent down: %s", e)

        # B4: Start agent again.
        # After a recent disconnect the platform WebSocket may take 30-60 s
        # to accept the reconnection.  If the Phoenix Channels client gives
        # up (10 rapid-disconnect limit) the process exits with code 1, so
        # we retry the launch.
        started = False
        for attempt in range(3):
            if attempt > 0:
                logger.info("Restart attempt %s/3 after %ss delay", attempt + 1, 5)
                await asyncio.sleep(5.0)
            if await runner.start(timeout=120.0):
                started = True
                break

        if not started:
            restart_logs = (
                runner.get_stderr()[-500:] if runner.get_stderr() else "(no stderr)"
            )
            logger.error("Restart failed after retries. stderr:\n%s", restart_logs)
            result.add_step(
                action="Restart agent",
                expected="Agent starts and reconnects",
                actual=f"started=False, stderr: {restart_logs[:200]}",
                status=Status.FAIL,
            )
            result.mark_fail("Agent failed to restart")
            return result

        result.add_step(
            action="Restart agent",
            expected="Agent starts and reconnects",
            actual=f"started={started}",
            status=Status.PASS,
        )

        # B5: Check for unsolicited replay messages after restart.
        # Acceptable new messages = pending recall (1) + any unanswered
        # pre-kill messages (slow tool responses still in flight).
        # Anything beyond that is an unsolicited replay.
        allowed_new = 1 + unanswered_before_kill
        await asyncio.sleep(15.0)

        messages_after_restart = await client.get_messages(room_id, page_size=100)
        new_msgs = _new_agent_msgs(
            messages_after_restart,
            agent.agent_id,
            known_ids,
            text_only=True,
        )
        known_ids.update(m["id"] for m in messages_after_restart)

        pending_reply = None
        for m in new_msgs:
            if pending_reply is None and secret in m.get("content", "").lower():
                pending_reply = m

        total_new = len(new_msgs)
        unsolicited_count = max(0, total_new - allowed_new)

        if unsolicited_count > 0:
            result.add_step(
                action="No unsolicited messages after restart",
                expected=f"At most {allowed_new} new message(s) (1 pending + {unanswered_before_kill} in-flight)",
                actual=(
                    f"FAIL: {total_new} new message(s) — "
                    f"{unsolicited_count} unsolicited beyond {allowed_new} allowed"
                ),
                status=Status.FAIL,
            )
            result.mark_fail(
                f"Unsolicited messages after restart: {unsolicited_count} "
                f"extra message(s) replayed from rehydrated history"
            )
        else:
            result.add_step(
                action="No unsolicited messages after restart",
                expected=f"At most {allowed_new} new message(s) (1 pending + {unanswered_before_kill} in-flight)",
                actual=f"{total_new} new message(s), 0 unsolicited",
                status=Status.PASS,
            )

        # B6: Response to pending message (sent while down).
        # The agent may have already replied during rehydration (detected
        # above), or it may reply later when we poll.
        if pending_reply:
            result.add_step(
                action="Post-restart recall (message sent while down)",
                expected=f"Agent recalls '{secret}' from transcript history",
                actual=pending_reply.get("content", "")[:200],
                status=Status.PASS,
            )
        else:
            try:
                response = await client.wait_for_agent_activity(
                    room_id,
                    agent.agent_id,
                    known_ids,
                    timeout=480.0,
                    settle_time=8.0,
                )

                if response:
                    known_ids.add(response["id"])
                    content = response.get("content", "")
                    has_secret = secret in content.lower()
                    result.add_step(
                        action="Post-restart recall (message sent while down)",
                        expected=f"Agent recalls '{secret}' from transcript history",
                        actual=content[:200],
                        status=Status.PASS if has_secret else Status.PARTIAL,
                    )
                else:
                    result.add_step(
                        action="Post-restart recall (message sent while down)",
                        expected="Agent responds to pending message",
                        actual="NO RESPONSE",
                        status=Status.FAIL,
                    )
            except Exception as e:
                result.add_step(
                    action="Post-restart recall (message sent while down)",
                    expected="Agent responds to pending message",
                    actual=f"ERROR: {e}",
                    status=Status.FAIL,
                )

        # B7: Post-restart conversation recall (new message sent after restart)
        try:
            send_resp = await client.send_message(
                room_id,
                "Summarize everything we discussed before the restart.",
                agent,
            )
            if "id" in send_resp:
                known_ids.add(send_resp["id"])

            response = await client.wait_for_agent_activity(
                room_id,
                agent.agent_id,
                known_ids,
                timeout=120.0,
                settle_time=8.0,
            )

            if response:
                known_ids.add(response["id"])
                content = response.get("content", "")
                has_secret = secret in content.lower()
                result.add_step(
                    action="Post-restart conversation recall",
                    expected=f"Agent summarizes pre-restart conversation including '{secret}'",
                    actual=content[:200],
                    status=Status.PASS if has_secret else Status.PARTIAL,
                )
            else:
                result.add_step(
                    action="Post-restart conversation recall",
                    expected="Agent summarizes pre-restart conversation",
                    actual="NO RESPONSE",
                    status=Status.FAIL,
                )
        except Exception as e:
            result.add_step(
                action="Post-restart conversation recall",
                expected="Agent summarizes pre-restart conversation",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )

        # B8: No extra responses after recall
        await asyncio.sleep(15.0)

        all_messages = await client.get_messages(room_id, page_size=100)
        extra = _new_agent_msgs(
            all_messages,
            agent.agent_id,
            known_ids,
            text_only=True,
        )
        known_ids.update(m["id"] for m in all_messages)

        if extra:
            result.add_step(
                action="No extra responses after recall",
                expected="0 extra text messages",
                actual=f"FAIL: {len(extra)} extra text response(s)",
                status=Status.FAIL,
            )
        else:
            result.add_step(
                action="No extra responses after recall",
                expected="0 extra messages",
                actual="No extra responses",
                status=Status.PASS,
            )

        result.finalize()
        return result
