from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import pytest

from remote_agent.network import HTTPRoom
from remote_agent.service import ConfigurationError, load_config
from remote_agent.store import Store
from tests.contracts.runtime.processgate import ProcessGate
from tests.contracts.runtime.service import ServiceFixture
from tests.paths import CONTRACT_RUNTIME_TESTS


@pytest.mark.contract_elixir
class TestService(ServiceFixture):
    @pytest.mark.contract_linux
    def test_ephemeral_agent_configuration_is_explicit_and_room_authority_remains_durable(
        self,
    ) -> None:
        config = self.agent_config("http://127.0.0.1:4567")
        config["store"]["durable"] = False
        parsed = load_config(self.private("ephemeral.json", config))
        self.assertFalse(parsed["store"]["durable"])
        room = self.room_config()
        room["store"]["durable"] = False
        with self.assertRaisesRegex(ConfigurationError, "room authority"):
            load_config(self.private("ephemeral-room.json", room))

    @pytest.mark.contract_linux
    def test_initial_route_is_an_explicit_validated_host_setting(self) -> None:
        config = self.agent_config("http://127.0.0.1:4567")
        for route in ("program", "llm"):
            config["agent"]["initial_route"] = route
            parsed = load_config(self.private("initial-route.json", config))
            self.assertEqual(parsed["agent"]["initial_route"], route)
        for route in ("hybrid", True, 7):
            config["agent"]["initial_route"] = route
            with self.assertRaisesRegex(ConfigurationError, "initial.*route"):
                load_config(self.private("invalid-route.json", config))

    @pytest.mark.contract_runtime
    @pytest.mark.contract_engine
    def test_check_config_rejects_uncompilable_contract_with_code_and_line(
        self,
    ) -> None:
        original = self.contract.read_text()
        self.assertIn("contract @30m mentions 1 => expired\n", original)
        self.contract.write_text(
            original.replace("contract @30m mentions 1 => expired\n", "contract\n", 1)
        )
        result = self.run_command("check-config", self.room_config())
        self.assertEqual(result.returncode, 2, result.stderr)
        diagnostic = json.loads(result.stderr)
        self.assertEqual(diagnostic["error"]["code"], "contract_clock_required")
        self.assertEqual(diagnostic["error"]["line"], 1)
        self.assertFalse((self.root / "room/state.sqlite3").exists())

    @pytest.mark.contract_runtime
    @pytest.mark.contract_engine
    def test_serve_surfaces_compile_code_and_line_without_config_or_credentials(
        self,
    ) -> None:
        original = self.contract.read_text()
        self.assertIn("contract @30m mentions 1 => expired\n", original)
        self.contract.write_text(
            original.replace("contract @30m mentions 1 => expired\n", "contract\n", 1)
        )
        result = self.run_command("serve", self.room_config())
        self.assertEqual(result.returncode, 2, result.stderr)
        diagnostic = json.loads(result.stderr)
        self.assertEqual(diagnostic["error"]["code"], "contract_clock_required")
        self.assertEqual(diagnostic["error"]["line"], 1)
        self.assertNotIn(self.tokens, result.stderr)
        self.assertNotIn("u" * 32, result.stderr)
        self.assertNotIn(str(self.contract), result.stderr)
        self.assertNotIn("service_ready", result.stdout)

    @pytest.mark.contract_runtime
    @pytest.mark.contract_engine
    def test_check_config_compiles_valid_contract_without_creating_room_store(
        self,
    ) -> None:
        result = self.run_command("check-config", self.room_config())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"valid": True, "kind": "room"})
        self.assertFalse((self.root / "room/state.sqlite3").exists())

    @pytest.mark.contract_runtime
    @pytest.mark.contract_engine
    def test_room_service_restart_preserves_receipt_and_offline_expiry(self) -> None:
        raw = self.contract.read_text()
        self.assertIn(
            "contract @30m", raw, "expiry mutation must hit the current fixture"
        )
        self.assertIn(
            "| @5m & stalled", raw, "owed-answer mutation must hit the current fixture"
        )
        self.contract.write_text(
            raw.replace("contract @30m", "contract @3s", 1).replace(
                "| @5m & stalled", "| @2s & stalled", 1
            )
        )
        config = self.room_config()
        clock = self.root / "room-clock"
        clock.write_text("1000", encoding="ascii")
        process, url = self.launch(config, "room1", clock=clock)
        client = HTTPRoom(url, "u" * 32)
        original = client.publish(self.question(), "user")
        self.assertEqual(original["status"], "accepted")
        self.stop(process)
        clock.write_text("4100", encoding="ascii")
        process, url = self.launch(config, "room2", clock=clock)
        client = HTTPRoom(url, "u" * 32)
        self.assertEqual(
            client.publish(self.question(), "user"),
            {"status": "duplicate", "original": original},
        )
        self.assertEqual(client.view("user")["view"]["status"], "terminal")
        self.stop(process)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_agent_service_polls_missed_input_and_restart_does_not_repeat_completed_turns(
        self,
    ) -> None:
        room, url = self.launch(self.room_config(), "room")
        client = HTTPRoom(url, "u" * 32)
        config = self.agent_config(url)
        agent, _ = self.launch(config, "agent1")
        client.publish(self.question(), "user")
        self.await_version(client, 2)
        self.stop(agent)
        client.publish(self.question("q2", 2), "user")
        agent, _ = self.launch(config, "agent2")
        self.await_version(client, 4)
        self.stop(agent)
        with Store(self.root / "agent/state.sqlite3") as store:
            self.assertEqual(
                (store.get("turns"), store.get("model_calls"), store.get("state")),
                (2, 0, {"count": 2}),
            )
            self.assertGreater(store.get("inbox_cursor"), 0)
        self.stop(room)

    @pytest.mark.contract_isolation
    @pytest.mark.contract_engine
    def test_shutdown_retains_completed_model_output_and_restart_reuses_it(
        self,
    ) -> None:
        room, url = self.launch(self.room_config(), "room")
        model_gate, stop_gate = ProcessGate(), ProcessGate()
        self.addCleanup(model_gate.close)
        self.addCleanup(stop_gate.close)
        provider = [
            sys.executable,
            str(CONTRACT_RUNTIME_TESTS / "providerpeer.py"),
            str(model_gate.port),
            model_gate.token,
        ]
        config = self.agent_config(url, provider)
        config["agent"]["model_timeout_ms"] = 10_000
        agent, _ = self.launch(config, "agent1", stopping=stop_gate)
        client = HTTPRoom(url, "u" * 32)
        client.publish(self.question(text="needs language judgment"), "user")
        model_gate.await_entry()
        agent.send_signal(signal.SIGTERM)
        stop_gate.await_entry()
        self.assertIsNone(agent.poll())
        model_gate.release()
        self.assertEqual(agent.wait(timeout=6), 0)
        with Store(self.root / "agent/state.sqlite3") as store:
            self.assertEqual(store.get("model_calls"), 1)
            self.assertEqual(store.get("turns"), 0)
            self.assertIsNotNone(store.get("staged"))
        agent, _ = self.launch(config, "agent2")
        self.await_version(client, 2)
        self.stop(agent)
        with Store(self.root / "agent/state.sqlite3") as store:
            self.assertEqual((store.get("model_calls"), store.get("turns")), (1, 1))
        self.stop(room)

    @pytest.mark.contract_linux
    def test_room_configuration_accepts_a_capability_list_and_rejects_malformed_ones(
        self,
    ) -> None:
        config = self.room_config()
        self.assertIsNone(
            load_config(self.private("plain.json", config))["room"]["capabilities"]
        )
        for stated in ([], ["contract.replies/1"]):
            config["room"]["capabilities"] = stated
            self.assertEqual(
                load_config(self.private("caps.json", config))["room"]["capabilities"],
                stated,
            )
        for bad in (
            "contract.replies/1",
            [1],
            ["contract.replies/1"] * 2,
            ["Contract Replies"],
            ["contract.replies/1", None],
            [f"x.{n}/1" for n in range(17)],
        ):
            config["room"]["capabilities"] = bad
            with self.assertRaises(ConfigurationError):
                load_config(self.private("bad-caps.json", config))

    @pytest.mark.contract_linux
    def test_configuration_rejects_unknown_fields_and_public_secret_files_before_startup(
        self,
    ) -> None:
        config = self.room_config()
        config["unknown"] = "private-sentinel"
        path = self.private("bad.json", config)
        with self.assertRaises(ConfigurationError):
            load_config(path)
        del config["unknown"]
        path = self.private("bad.json", config)
        Path(path).chmod(0o644)
        with self.assertRaises(ConfigurationError):
            load_config(path)
        self.assertFalse((self.root / "room/state.sqlite3").exists())
