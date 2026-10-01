"""Offline failure/report checks; no credentials or live platform are used."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

import httpx
from band_rest import AsyncRestClient

from band.runtime.tools.agent import AgentTools
from tests.paths import REPO_ROOT

SCRIPTS = REPO_ROOT / "scripts" / "platform-compat"


class PlatformCompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.results = Path(self.directory.name)
        spec = importlib.util.spec_from_file_location(
            "compat_smoke", SCRIPTS / "smoke.py"
        )
        self.smoke = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.smoke
        with patch.object(sys, "argv", ["smoke.py", str(REPO_ROOT)]):
            spec.loader.exec_module(self.smoke)

    def report(self) -> dict[str, Any]:
        return json.loads((self.results / "results.json").read_text())["scenarios"][0]

    def test_missing_config_writes_sanitized_incomplete(self) -> None:
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "smoke.py"), str(REPO_ROOT)],
            env={"RESULTS_DIR": str(self.results), "BAND_API_KEY": "fake-secret"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.report()["outcome"], "incomplete")
        self.assertNotIn("fake-secret", result.stdout + result.stderr)
        self.assertIsNotNone(
            ElementTree.parse(self.results / "junit.xml").find("testcase/error")
        )

    def test_fallback_report_exists_without_sdk_import(self) -> None:
        subprocess.run(
            [sys.executable, "-I", str(SCRIPTS / "report.py"), str(self.results)],
            check=True,
        )
        self.assertEqual(self.report()["category"], "setup")
        self.assertEqual(self.report()["outcome"], "incomplete")

    def settings(self) -> Any:
        return self.smoke.Settings(
            results_dir=self.results,
            band_rest_url="http://127.0.0.1:4000",
            band_base_url="http://127.0.0.1:4000",
            band_ws_url="ws://127.0.0.1:4000/api/v1/socket/websocket",
            band_api_key="fake-agent",
            band_api_key_user="fake-user",
            test_agent_id="seeded-agent",
        )

    def test_production_endpoint_rejected(self) -> None:
        settings = self.settings()
        settings.band_rest_url = "https://api.band.ai"
        with self.assertRaises(ValueError):
            settings.validate_endpoints()

    def exercise(
        self,
        *,
        wrong_sender: bool = False,
        wrong_content: bool = False,
        wrong_id: bool = False,
        setup_failure: bool = False,
        cleanup_failure: bool = False,
    ) -> dict[str, Any]:
        row = {"outcome": "incomplete", "category": "setup"}
        user = SimpleNamespace(
            create_room=AsyncMock(return_value="new-room"),
            add_participant=AsyncMock(),
            delete_room=AsyncMock(),
            send_message=AsyncMock(),
            list_messages=AsyncMock(),
        )
        agent = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        if setup_failure:
            user.add_participant.side_effect = RuntimeError("setup failure")
        if cleanup_failure:
            user.delete_room.side_effect = RuntimeError("cleanup failure")

        def build_agent(**kwargs: Any) -> SimpleNamespace:
            adapter = kwargs["adapter"]
            adapter.reply_id = "agent-reply"
            adapter.expected_content = f"@[[seeded-user]] {adapter.token}"
            adapter.received.set()
            user.list_messages.return_value = [
                SimpleNamespace(
                    sender_id="wrong-agent" if wrong_sender else "seeded-agent",
                    id="other-message" if wrong_id else adapter.reply_id,
                    content=adapter.token
                    if wrong_content
                    else adapter.expected_content,
                )
            ]
            return agent

        with (
            patch.object(self.smoke.Agent, "create", side_effect=build_agent),
            patch.object(self.smoke, "UserOps", return_value=user),
        ):
            if setup_failure or wrong_sender or wrong_content or wrong_id:
                with self.assertRaises((RuntimeError, AssertionError)):
                    asyncio.run(self.smoke.scenario(self.settings(), row))
            else:
                asyncio.run(self.smoke.scenario(self.settings(), row))
        user.delete_room.assert_awaited_once_with("new-room")
        agent.stop.assert_awaited_once()
        return row

    def test_roundtrip_checks_persisted_sender(self) -> None:
        self.exercise(wrong_sender=True)

    def test_roundtrip_checks_exact_mention_content(self) -> None:
        self.exercise(wrong_content=True)

    def test_roundtrip_checks_returned_message_id(self) -> None:
        self.exercise(wrong_id=True)

    def test_adapter_records_message_id_and_mention_placeholder(self) -> None:
        adapter = self.smoke.RoundtripAdapter("unique-probe")

        async def roundtrip() -> None:
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    201,
                    json={
                        "data": {
                            "id": "sent-message",
                            "success": True,
                            "recipients": [],
                        }
                    },
                )
            )
            async with httpx.AsyncClient(transport=transport) as client:
                rest = AsyncRestClient(
                    base_url="http://localhost",
                    api_key="fake-agent",
                    httpx_client=client,
                )
                tools = AgentTools(
                    room_id="test-room",
                    rest=rest,
                    participants=[{"id": "test-user", "handle": "test-user"}],
                )
                await adapter.on_message(
                    SimpleNamespace(content="unique-probe", sender_id="test-user"),
                    tools,
                    [],
                    None,
                    None,
                    is_session_bootstrap=False,
                    room_id="test-room",
                )

        asyncio.run(roundtrip())
        self.assertEqual(
            (adapter.reply_id, adapter.expected_content, adapter.received.is_set()),
            ("sent-message", "@[[test-user]] unique-probe", True),
        )

    def test_callback_failures_wake_waiter_without_exposing_exception_body(
        self,
    ) -> None:
        for result, error, reason in (
            (None, None, "SDK send_message returned no reply"),
            (
                None,
                RuntimeError("secret-api-body"),
                "SDK reply callback failed (RuntimeError)",
            ),
        ):
            with self.subTest(reason=reason):
                adapter = self.smoke.RoundtripAdapter("probe")
                tools = SimpleNamespace(
                    send_message=AsyncMock(return_value=result, side_effect=error)
                )

                async def exercise_failure(adapter: Any, tools: Any) -> None:
                    with self.assertRaises(RuntimeError):
                        await adapter.on_message(
                            SimpleNamespace(content="probe", sender_id="test-user"),
                            tools,
                            [],
                            None,
                            None,
                            is_session_bootstrap=False,
                            room_id="test-room",
                        )
                    await asyncio.wait_for(adapter.received.wait(), timeout=0.1)

                asyncio.run(exercise_failure(adapter, tools))
                self.assertEqual(adapter.failure_reason, reason)

    def test_setup_failure_still_cleans_room(self) -> None:
        self.exercise(setup_failure=True)

    def test_cleanup_failure_prevents_pass(self) -> None:
        row = self.exercise(cleanup_failure=True)
        self.assertEqual((row["outcome"], row["cleanup"]), ("fail", "failed"))

    def test_roundtrip_with_cleanup_passes(self) -> None:
        row = self.exercise()
        self.assertEqual((row["outcome"], row["cleanup"]), ("pass", "pass"))


if __name__ == "__main__":
    unittest.main()
