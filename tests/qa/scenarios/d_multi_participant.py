from __future__ import annotations

import asyncio
import logging
import time

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)


def _mention_ids(msg: dict) -> set[str]:
    """IDs this message @mentions (from metadata.mentions)."""
    metadata = msg.get("metadata") or {}
    mentions = metadata.get("mentions") or []
    return {m.get("id") for m in mentions if isinstance(m, dict) and m.get("id")}


def _ref(agent: AgentInfo) -> str:
    """How to address an agent in free text — prefer handle, then name, then id."""
    return agent.handle or agent.name or agent.agent_id


class MultiParticipantScenario(Scenario):
    """Multi-directional conversation between two agents in one room.

    Beyond checking that both agents answer a direct mention, this exercises the
    delegation/relaying contract in two patterns, each in both directions and
    each in its own room:

    - direct delivery: the user asks the asker to have the helper answer the
      user DIRECTLY (asker @mentions helper -> helper @mentions the user).
    - relay-back: the user asks the asker to consult the helper and then relay
      the answer back themselves (asker @mentions helper -> helper replies ->
      asker @mentions the user).

    This is the multi-agent behavior single-agent scenarios cannot cover.
    """

    name = "D: Multi-Participant"
    description = "Two agents holding a multi-directional, agent-to-agent conversation"

    def __init__(
        self,
        second_agent_id: str,
        second_example_file: str,
        second_runner: AgentRunner | None = None,
    ) -> None:
        self.second_agent_id = second_agent_id
        self.second_example_file = second_example_file
        self.second_runner = second_runner

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)

        user_id = await client.get_user_id()

        # --- Setup: fresh room with both agents ----------------------------
        try:
            new_room_id = await client.create_room()
            agent_a = await client.add_participant(new_room_id, agent.agent_id)
            agent_b = await client.add_participant(new_room_id, self.second_agent_id)
            result.note_room(new_room_id)
            result.add_step(
                action="Create room and add both agents",
                expected="Room with agent A and agent B",
                actual=f"room={new_room_id}, A={_ref(agent_a)}, B={_ref(agent_b)}",
                status=Status.PASS,
            )
        except Exception as e:
            result.mark_fail(f"Setup failed: {e}")
            return result

        await asyncio.sleep(3.0)

        known_ids: set[str] = {
            m["id"] for m in await client.get_messages(new_room_id, page_size=100)
        }

        # --- 1. Parallel: both respond to a single multi-mention -----------
        a_name = agent_a.name or "Agent A"
        b_name = agent_b.name or "Agent B"
        try:
            await self._post(
                client,
                new_room_id,
                f"@{a_name} please say hello, and @{b_name} please introduce yourself.",
                [agent_a, agent_b],
                known_ids,
            )
            msgs = await self._collect(client, new_room_id, known_ids, timeout=120.0)
            got_a = self._spoke(msgs, agent_a.agent_id)
            got_b = self._spoke(msgs, agent_b.agent_id)
            result.add_step(
                action="Both agents answer a direct multi-mention",
                expected="Agent A and Agent B both reply",
                actual=f"A_replied={got_a}, B_replied={got_b}",
                status=Status.PASS if (got_a and got_b) else Status.PARTIAL,
            )
        except Exception as e:
            result.add_step(
                action="Both agents answer a direct multi-mention",
                expected="Agent A and Agent B both reply",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )

        # --- Direct delivery: asker delegates, helper answers the user -----
        # (own room each, no cross-contamination)
        await self._relay_phase(
            client,
            result,
            user_id,
            mode="direct",
            asker=agent_a,
            helper=agent_b,
            label="A->B->user",
        )
        await self._relay_phase(
            client,
            result,
            user_id,
            mode="direct",
            asker=agent_b,
            helper=agent_a,
            label="B->A->user",
        )

        # --- Relay-back: asker asks helper, helper replies to asker, asker --
        # summarizes the answer back to the user.
        await self._relay_phase(
            client,
            result,
            user_id,
            mode="relay_back",
            asker=agent_a,
            helper=agent_b,
            label="A->B->A->user",
        )
        await self._relay_phase(
            client,
            result,
            user_id,
            mode="relay_back",
            asker=agent_b,
            helper=agent_a,
            label="B->A->B->user",
        )

        # Overall status reflects the weakest step — PARTIAL relays must surface
        # (finalize() only looks at FAIL counts and would hide them).
        statuses = [s.status for s in result.steps]
        if statuses and all(s == Status.PASS for s in statuses):
            result.status = Status.PASS
        elif statuses and all(s == Status.FAIL for s in statuses):
            result.status = Status.FAIL
        else:
            result.status = Status.PARTIAL
        return result

    # ------------------------------------------------------------------ #

    async def _relay_phase(
        self,
        client: PlatformClient,
        result: ScenarioResult,
        user_id: str,
        *,
        mode: str,
        asker: AgentInfo,
        helper: AgentInfo,
        label: str,
    ) -> None:
        """Run one multi-agent coordination phase in its own fresh room.

        mode="direct": the user asks `asker` to have `helper` answer the user
            DIRECTLY. Success chain: asker @mentions helper -> helper @mentions
            the user with the answer.
        mode="relay_back": the user asks `asker` to consult `helper` and then
            relay the answer back themselves. Success chain: asker @mentions
            helper -> helper replies -> asker @mentions the user with the answer.

        Each phase uses its own room so phases cannot contaminate each other.
        """
        try:
            room_id = await client.create_room()
            asker = await client.add_participant(room_id, asker.agent_id)
            helper = await client.add_participant(room_id, helper.agent_id)
            result.note_room(room_id)
        except Exception as e:
            result.add_step(
                action=f"{label}: set up room",
                expected="Room with asker + helper",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )
            return

        await asyncio.sleep(3.0)
        known_ids: set[str] = {
            m["id"] for m in await client.get_messages(room_id, page_size=100)
        }

        # The user must be addressable by handle. The profile endpoint omits the
        # handle, so read it from the room participant list.
        user = await self._find_user(client, room_id, user_id)
        user_ref = (user.handle if user else None) or "the person who asked"
        user_name = (user.name if user else None) or "the user"
        helper_name = helper.name or _ref(helper)

        topic = "a one-sentence fun fact"
        if mode == "direct":
            instruction = (
                f"Please coordinate with @{_ref(helper)} (also in this room): ask "
                f"them for {topic} and have them send it DIRECTLY to me — @mention "
                f"me (@{user_ref}) with the fun fact. Do not answer it yourself and "
                f"do not relay it for them; {helper_name} should @mention "
                f"{user_name} themselves with the answer."
            )
            action_desc = "asker delegates; helper answers the user directly"
            expected = (
                "asker @mentions helper -> helper @mentions the user with the answer"
            )
        else:  # relay_back
            instruction = (
                f"Please coordinate with @{_ref(helper)} (also in this room): ask "
                f"them for {topic} and have them reply to YOU. Once {helper_name} "
                f"replies, summarize their answer and send it to me (@{user_ref}) "
                f"yourself. Do NOT ask {helper_name} to message me directly — they "
                f"should answer you, and you relay their answer to me."
            )
            action_desc = "asker consults helper, then relays the answer to the user"
            expected = (
                "asker @mentions helper -> helper replies -> "
                "asker @mentions the user with the answer"
            )

        try:
            await self._post(client, room_id, instruction, [asker], known_ids)
        except Exception as e:
            result.add_step(
                action=f"{label}: send request to asker",
                expected="Request delivered",
                actual=f"ERROR: {e}",
                status=Status.FAIL,
            )
            return

        # Multi-turn coordination on slow Parlant: allow a long quiet window so
        # the full chain can complete before we conclude.
        msgs = await self._collect(
            client, room_id, known_ids, timeout=300.0, settle=25.0
        )

        delegated = self._first_time(
            msgs, sender=asker.agent_id, mentions=helper.agent_id
        )
        if mode == "direct":
            # Success = the HELPER delivers the fun fact straight to the user.
            final = self._first_time(
                msgs,
                sender=helper.agent_id,
                mentions=user_id,
                kind="text",
                after=delegated,
            )
            checks = [
                ("asker_delegated", delegated is not None),
                ("helper_answered_user", final is not None),
            ]
        else:  # relay_back
            helper_replied = self._first_time(
                msgs, sender=helper.agent_id, kind="text", after=delegated
            )
            # Success = the ASKER relays the answer back to the user.
            final = self._first_time(
                msgs,
                sender=asker.agent_id,
                mentions=user_id,
                kind="text",
                after=helper_replied,
            )
            checks = [
                ("asker_asked_helper", delegated is not None),
                ("helper_replied", helper_replied is not None),
                ("asker_relayed_to_user", final is not None),
            ]

        oks = [ok for _, ok in checks]
        if all(oks):
            status = Status.PASS
        elif not any(oks):
            status = Status.FAIL
        else:
            status = Status.PARTIAL

        final_text = ""
        if final is not None:
            final_text = next(
                ((m.get("content") or "") for m in msgs if m["id"] == final[1]),
                "",
            )[:150]

        actual = ", ".join(f"{name}={ok}" for name, ok in checks)
        if final_text:
            actual += f' | "{final_text}"'

        result.add_step(
            action=f"{label} (room {room_id[:8]}): {action_desc}",
            expected=expected,
            actual=actual,
            status=status,
        )

    # ------------------------------------------------------------------ #

    async def _post(
        self,
        client: PlatformClient,
        room_id: str,
        content: str,
        agents: list[AgentInfo],
        known_ids: set[str],
    ) -> None:
        mentions = []
        for a in agents:
            mention: dict[str, str] = {"id": a.agent_id}
            if a.handle:
                mention["handle"] = a.handle
            if a.name:
                mention["name"] = a.name
            mentions.append(mention)

        resp = await client._client.post(
            f"/api/v1/me/chats/{room_id}/messages",
            json={"message": {"content": content, "mentions": mentions}},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        if "id" in data:
            known_ids.add(data["id"])
        logger.info("Posted to room %s: %s", room_id, content[:80])

    @staticmethod
    async def _find_user(
        client: PlatformClient, room_id: str, user_id: str
    ) -> AgentInfo | None:
        """Resolve the user participant (for their handle/name) from the room."""
        try:
            for p in await client.list_participants(room_id):
                if p.get("id") == user_id:
                    return AgentInfo(
                        agent_id=user_id, handle=p.get("handle"), name=p.get("name")
                    )
        except Exception as e:
            logger.warning("Could not resolve user participant: %s", e)
        return None

    async def _collect(
        self,
        client: PlatformClient,
        room_id: str,
        known_ids: set[str],
        *,
        timeout: float,
        settle: float = 10.0,
        poll: float = 3.0,
    ) -> list[dict]:
        """Collect new agent messages until `settle`s of quiet or `timeout`."""
        start = time.monotonic()
        last_new = time.monotonic()
        collected: list[dict] = []

        while time.monotonic() - start < timeout:
            messages = await client.get_messages(room_id, page_size=100)
            fresh = [
                m
                for m in messages
                if m["id"] not in known_ids and m.get("sender_type") == "Agent"
            ]
            if fresh:
                for m in sorted(fresh, key=lambda x: x.get("inserted_at", "")):
                    known_ids.add(m["id"])
                    collected.append(m)
                last_new = time.monotonic()
            if collected and (time.monotonic() - last_new >= settle):
                break
            await asyncio.sleep(poll)

        return collected

    @staticmethod
    def _spoke(msgs: list[dict], sender_id: str) -> bool:
        return any(
            m.get("sender_id") == sender_id and m.get("message_type") == "text"
            for m in msgs
        )

    @staticmethod
    def _first_time(
        msgs: list[dict],
        *,
        sender: str,
        mentions: str | None = None,
        kind: str | None = None,
        after: tuple[str, str] | None = None,
    ) -> tuple[str, str] | None:
        """Return (inserted_at, id) of the earliest message matching the filters.

        `after` is another (inserted_at, id) tuple — only messages at or after
        that timestamp qualify (used to enforce ordering in a relay chain).
        """
        best: tuple[str, str] | None = None
        for m in msgs:
            if m.get("sender_id") != sender:
                continue
            if kind is not None and m.get("message_type") != kind:
                continue
            if mentions is not None and mentions not in _mention_ids(m):
                continue
            ts = m.get("inserted_at", "")
            if after is not None and ts < after[0]:
                continue
            if best is None or ts < best[0]:
                best = (ts, m["id"])
        return best
