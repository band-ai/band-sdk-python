from __future__ import annotations

import asyncio
import json
import logging

from harness.api_client import AgentInfo, PlatformClient
from harness.agent_runner import AgentRunner
from harness.scenario import Scenario, ScenarioResult, Status

logger = logging.getLogger(__name__)

PERSONAL_INFO = "My name is Quinn and my favorite color is teal"
UPDATED_INFO = "My name is Quinn and my favorite color is green"


class MemoryToolsScenario(Scenario):
    name = "E: Memory Tools"
    description = (
        "Memory lifecycle (store / list / get / supersede / archive) "
        "and cross-room persistence"
    )

    async def run(
        self,
        client: PlatformClient,
        runner: AgentRunner,
        agent: AgentInfo,
        room_id: str,
    ) -> ScenarioResult:
        result = ScenarioResult(name=self.name, description=self.description)
        known: set[str] = set()
        for m in await client.get_messages(room_id):
            known.add(m["id"])

        resp = await self._ask(
            client, room_id, agent, known,
            f"Please store this information as a memory: {PERSONAL_INFO}. "
            "Use system 'long_term', type 'semantic', segment 'user'.",
        )
        self._check(result, resp,
            action="Store personal info as memory",
            expected="Agent calls thenvoi_store_memory and confirms",
            keywords=["stored", "saved", "memory", "created"],
        )

        resp = await self._ask(
            client, room_id, agent, known,
            "List all my memories. What do you have stored?",
        )
        if resp:
            c = resp.get("content", "").lower()
            ok = "quinn" in c and "teal" in c
            result.add_step(
                action="List memories (same room)",
                expected="Response includes Quinn + teal",
                actual=resp.get("content", "")[:200],
                status=Status.PASS if ok else Status.PARTIAL,
            )
        else:
            result.add_step(
                action="List memories (same room)",
                expected="Memory list returned",
                actual="NO RESPONSE",
                status=Status.FAIL,
            )

        resp = await self._ask(
            client, room_id, agent, known,
            "Get the full details of the memory you just stored about me.",
        )
        self._check(result, resp,
            action="Get specific memory details",
            expected="Agent returns memory content (Quinn)",
            keywords=["quinn"],
        )

        await self._cross_room_check(client, agent, result)

        resp = await self._ask(
            client, room_id, agent, known,
            "My favorite color changed. Please supersede the old memory about my color.",
        )
        self._check(result, resp,
            action="Supersede old memory",
            expected="Agent calls thenvoi_supersede_memory",
            keywords=["supersed", "replaced", "updated", "marked"],
        )

        resp = await self._ask(
            client, room_id, agent, known,
            f"Store a new memory: {UPDATED_INFO}. Same settings as before.",
        )
        self._check(result, resp,
            action="Store updated memory (green)",
            expected="New memory stored",
            keywords=["stored", "saved", "created"],
        )

        resp = await self._ask(
            client, room_id, agent, known,
            "Archive the old superseded memory about teal.",
        )
        self._check(result, resp,
            action="Archive superseded memory",
            expected="Agent calls thenvoi_archive_memory",
            keywords=["archived", "archive"],
        )

        resp = await self._ask(
            client, room_id, agent, known,
            "List all active memories. How many do I have? What's my current favorite color?",
        )
        if resp:
            c = resp.get("content", "").lower()
            has_green = "green" in c
            teal_gone = "teal" not in c or any(w in c for w in ["supersed", "archived", "old"])
            result.add_step(
                action="Final memory list",
                expected="Only green memory active; teal superseded/archived",
                actual=resp.get("content", "")[:200],
                status=Status.PASS if (has_green and teal_gone) else Status.PARTIAL,
            )
        else:
            result.add_step(
                action="Final memory list",
                expected="Updated memory list",
                actual="NO RESPONSE",
                status=Status.FAIL,
            )

        result.finalize()
        return result

    async def _cross_room_check(
        self,
        client: PlatformClient,
        agent: AgentInfo,
        result: ScenarioResult,
    ) -> None:
        try:
            new_room = await client.create_room()
            await client.add_participant(new_room, agent.agent_id)
            await asyncio.sleep(3.0)

            cr_known: set[str] = {m["id"] for m in await client.get_messages(new_room)}

            resp = await self._ask(
                client, new_room, agent, cr_known,
                "Check your stored memories — what do you know about me? "
                "What's my name and favorite color?",
            )
            if resp:
                c = resp.get("content", "").lower()
                ok = "quinn" in c and "teal" in c
                result.add_step(
                    action="Cross-room memory recall",
                    expected="Agent finds memory from other room (Quinn, teal)",
                    actual=resp.get("content", "")[:200],
                    status=Status.PASS if ok else Status.FAIL,
                )
            else:
                result.add_step(
                    action="Cross-room memory recall",
                    expected="Agent recalls personal info via memory tools",
                    actual="NO RESPONSE",
                    status=Status.FAIL,
                )
        except Exception as exc:
            result.add_step(
                action="Cross-room memory check",
                expected="Memory accessible from new room",
                actual=f"ERROR: {exc}",
                status=Status.FAIL,
            )

    async def _ask(
        self,
        client: PlatformClient,
        room_id: str,
        agent: AgentInfo,
        known: set[str],
        message: str,
        timeout: float = 120.0,
    ) -> dict | None:
        sent = await client.send_message(room_id, message, agent)
        if "id" in sent:
            known.add(sent["id"])
        resp = await client.wait_for_agent_activity(
            room_id, agent.agent_id, known, timeout=timeout, settle_time=8.0,
        )
        if resp and resp.get("message_type") != "text":
            resp = self._extract_tool_content(resp)
        return resp

    @staticmethod
    def _extract_tool_content(msg: dict) -> dict:
        raw = msg.get("content", "")
        try:
            parsed = json.loads(raw)
            output = parsed.get("output", raw)
            if isinstance(output, str):
                try:
                    inner = json.loads(output.replace("'", '"'))
                    if isinstance(inner, dict) and "result" in inner:
                        raw = str(inner["result"])
                    else:
                        raw = output
                except (json.JSONDecodeError, ValueError):
                    raw = output
        except (json.JSONDecodeError, TypeError):
            pass
        return {**msg, "content": raw, "_from_tool": True}

    @staticmethod
    def _check(
        result: ScenarioResult,
        resp: dict | None,
        *,
        action: str,
        expected: str,
        keywords: list[str],
    ) -> None:
        if resp:
            c = resp.get("content", "").lower()
            ok = any(kw in c for kw in keywords)
            result.add_step(
                action=action,
                expected=expected,
                actual=resp.get("content", "")[:200],
                status=Status.PASS if ok else Status.PARTIAL,
            )
        else:
            result.add_step(
                action=action,
                expected=expected,
                actual="NO RESPONSE",
                status=Status.FAIL,
            )
