from __future__ import annotations

import http.client
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.authority import DurableRoom
from remote_agent.controller import HybridAgent
from remote_agent.endpoint import Endpoint
from remote_agent.network import AgentAPI, HTTPRoom, HTTPService, RoomAPI
from remote_agent.program import Artifact, Program
from remote_agent.storage import StoreError
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, LocalGate, TransportError
from tests.contracts.runtime.commanddeadline import command_deadline
from tests.contracts.runtime.hybrid import ScriptedModel
from tests.contracts.runtime.network.kit import (
    LegacyInboxAPI,
    LegacyViewAPI,
    NetworkFixture,
)
from tests.contracts.runtime.program import SOURCE
from tests.contracts.runtime.timers import TimerClock, await_capacity
from tests.paths import CONTRACTS_ROOT


@pytest.mark.contract_linux
@pytest.mark.contract_engine
class TestNetwork(NetworkFixture):
    def test_raw_compact_wire_has_a_separate_strict_schema(self) -> None:
        compact_schema = self.wire_schema("http_compact_projection")
        full_schema = self.wire_schema("projection")
        status, compact = self.request("/v1/view", {})
        self.assertEqual(status, 200)
        compact_schema.validate(compact)
        self.assertFalse(
            full_schema.is_valid(compact),
            "Full engine projections must still require definition",
        )
        hydrated = HTTPRoom(self.server.url, "u" * 32).view("user")
        full_schema.validate(hydrated)
        self.assertFalse(
            compact_schema.is_valid(hydrated),
            "Compact wire must not resend the definition",
        )
        missing_checkpoint = dict(compact)
        del missing_checkpoint["checkpoint"]
        self.assertFalse(compact_schema.is_valid(missing_checkpoint))
        missing_duties = json.loads(json.dumps(compact))
        del missing_duties["view"]["obligations"]
        self.assertFalse(compact_schema.is_valid(missing_duties))

    def test_definition_wire_schema_rejects_invalid_bindings(self) -> None:
        schema = self.wire_schema("http_definition")
        status, definition = self.request("/v1/definition", method="GET")
        self.assertEqual(status, 200)
        schema.validate(definition)
        for field, invalid in (
            ("profile", "unbound/definition"),
            ("actor", None),
            ("session", ""),
            ("identity", {}),
            ("definition", {}),
        ):
            with self.subTest(field=field):
                self.assertFalse(schema.is_valid(dict(definition, **{field: invalid})))
        self.assertFalse(schema.is_valid(dict(definition, checkpoint="private")))

    def test_definition_authenticates_bound_role_without_changing_room_state(
        self,
    ) -> None:
        records = self.store.records()
        expected = self.room.view("user")["view"]["definition"]
        code, definition = self.request("/v1/definition", method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(
            definition,
            {
                "profile": "contract-room/definition/1",
                "session": "s",
                "identity": self.room.identity,
                "actor": "user",
                "definition": expected,
            },
        )
        self.assertEqual(self.store.records(), records)
        self.assertEqual(
            self.request("/v1/definition", method="GET", token="wrong")[0], 401
        )
        self.assertEqual(
            self.request("/v1/definition", method="GET", token="o" * 32)[0], 403
        )
        outsider = HTTPService(
            RoomAPI(self.room),
            {"x" * 32: {"actor": "outsider", "permissions": ["room"]}},
        )
        self.addCleanup(outsider.close)
        self.assertEqual(
            self.request(
                "/v1/definition", method="GET", token="x" * 32, server=outsider
            )[0],
            403,
        )
        self.assertEqual(self.request("/v1/definition", {}, method="POST")[0], 404)
        self.room.publish(self.question(), "user")
        self.assertEqual(
            self.request("/v1/definition", method="GET"), (code, definition)
        )
        _, assistant = self.request("/v1/definition", method="GET", token="a" * 32)
        self.assertEqual(
            assistant["definition"], self.room.view("agent")["view"]["definition"]
        )
        self.assertNotEqual(assistant["definition"]["role"], expected["role"])

    def test_compact_view_preserves_full_preflight_and_requires_empty_body(
        self,
    ) -> None:
        expected = self.room.view("user")
        del expected["view"]["definition"]
        self.assertEqual(self.request("/v1/view", {}), (200, expected))
        self.assertEqual(self.request("/v1/view", {"compact": True})[0], 400)
        self.assertEqual(self.request("/v1/view", {"actor": "agent"})[0], 400)

    def test_http_room_hydrates_definition_once_without_cache_mutation(self) -> None:
        with patch.object(HTTPRoom, "_request", autospec=True, wraps=None) as request:
            request.side_effect = lambda client, method, path, data=None: self.request(
                path, data, method=method, token="u" * 32
            )[1]
            client = HTTPRoom(self.server.url, "u" * 32)
            first = client.view("user")
            self.assertEqual(first, self.room.view("user"))
            first["view"]["definition"].clear()
            self.assertEqual(client.view("user"), self.room.view("user"))
            self.assertEqual(
                sum(
                    call.args[2] == "/v1/definition" for call in request.call_args_list
                ),
                1,
            )
            self.assertTrue(
                all(
                    call.args[3:] == ({},)
                    for call in request.call_args_list
                    if call.args[2] == "/v1/view"
                )
            )

    def test_http_room_still_accepts_legacy_full_view_authority(self) -> None:
        legacy = HTTPService(LegacyViewAPI(self.room), self.tokens)
        self.addCleanup(legacy.close)
        client = HTTPRoom(legacy.url, "u" * 32)
        self.assertEqual(client.view("user"), self.room.view("user"))

    def test_inbox_capability_is_negotiated_before_a_polling_client_starts(
        self,
    ) -> None:
        status, identity = self.request("/v1/identity", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(identity["inbox_profile"], "contract-room/inbox/1")
        legacy = HTTPService(LegacyInboxAPI(self.room), self.tokens)
        self.addCleanup(legacy.close)
        with self.assertRaisesRegex(TransportError, "inbox"):
            HTTPRoom(legacy.url, "a" * 32, require_inbox=True)
        client = HTTPRoom(legacy.url, "a" * 32)
        with self.assertRaisesRegex(TransportError, "inbox"):
            client.inbox(0)

    def test_durable_inbox_orders_visible_acceptances_and_replays_after_restart(
        self,
    ) -> None:
        client = HTTPRoom(self.server.url, "a" * 32)
        request = self.question()
        first = self.room.publish(request, "user")
        answer = {
            "id": "a1",
            "session": "s",
            "kind": "answer",
            "expected_version": 1,
            "payload": {"request": request["id"], "text": "pong"},
        }
        self.room.publish(answer, "agent")
        second = self.room.publish(dict(request, id="q2", expected_version=2), "user")
        page = client.inbox(0, limit=100)
        self.assertEqual(
            [event["id"] for event in page["events"]], [first["id"], second["id"]]
        )
        self.assertEqual(client.inbox(page["cursor"])["events"], [])
        self.assertEqual(client.inbox(0, limit=1)["events"], [])
        self.assertEqual(
            self.request("/v1/inbox", {"after": -1, "limit": 100}, token="a" * 32)[0],
            400,
        )
        self.assertEqual(
            self.request("/v1/inbox", {"after": 0, "limit": 1000}, token="a" * 32)[0],
            400,
        )
        self.assertEqual(
            self.request("/v1/inbox", {"after": 0, "limit": 100}, token="o" * 32)[0],
            403,
        )
        self.server.close()
        self.engine.close()
        self.store.close()
        self.store = Store(Path(self.folder.name) / "room.sqlite3")
        self.addCleanup(self.store.close)
        self.engine = EngineProcess(
            CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        )
        self.addCleanup(self.engine.close)
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="s",
            clock=lambda: 0,
            bindings={"requester": "user", "assistant": "agent"},
        )
        self.server = self.serve()
        client = HTTPRoom(self.server.url, "a" * 32)
        self.assertEqual(client.inbox(0), page)

    def test_inbox_reports_head_and_refuses_a_cursor_ahead_of_restored_history(
        self,
    ) -> None:
        self.room.publish(self.question(), "user")
        records = self.store.records()
        head = {"sequence": records[-1]["sequence"], "hash": records[-1]["hash"]}
        client = HTTPRoom(self.server.url, "a" * 32, require_inbox=True)
        code, body = self.request(
            "/v1/inbox", {"after": head["sequence"] + 10, "limit": 100}, token="a" * 32
        )
        self.assertEqual(
            (code, body),
            (
                409,
                {
                    "code": "cursor_ahead",
                    "error": "Inbox cursor is ahead of the room journal",
                },
            ),
        )
        with self.assertRaisesRegex(TransportError, "409"):
            client.inbox(head["sequence"] + 10)
        self.assertEqual(self.store.records(), records)
        self.assertEqual(
            client.inbox(head["sequence"]),
            {"cursor": head["sequence"], "events": [], "head": head},
        )
        first = client.inbox(0, limit=1)
        self.assertEqual(first, {"cursor": 1, "events": [], "head": head})
        page = client.inbox(first["cursor"])
        self.assertEqual(page["head"], head)
        self.assertEqual(page["cursor"], head["sequence"])
        self.assertEqual([event["id"] for event in page["events"]], ["q1"])

    def test_network_room_authenticates_actor_and_deduplicates_exact_proposals(
        self,
    ) -> None:
        client = HTTPRoom(self.server.url, "u" * 32)
        self.assertEqual(client.identity, self.room.identity)
        self.assertEqual(client.view("user", 999)["now"], 0)
        accepted = client.publish(self.question(), "user", 999)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(
            client.lookup(self.question(), "user", 999),
            {"status": "duplicate", "original": accepted},
        )
        with self.assertRaises(TransportError):
            client.publish(self.question(), "agent", 0)

    def test_invalid_authentication_and_insufficient_grants_do_not_touch_the_engine(
        self,
    ) -> None:
        self.assertEqual(
            self.request("/v1/proposals", self.question(), token="wrong")[0], 401
        )
        self.assertEqual(
            self.request("/v1/proposals", self.question(), token="o" * 32)[0], 403
        )
        result = self.request("/v1/proposals", self.question(), token="a" * 32)
        self.assertEqual(result[1]["status"], "rejected")
        self.assertEqual(self.room.view("user")["view"]["version"], 0)
        self.assertTrue(self.room.status()["ready"])

    def test_room_api_routes_before_permissions_and_rejects_nonempty_view_body(
        self,
    ) -> None:
        prior = self.store.records()
        for token in ("u" * 32, "o" * 32):
            for method, path in (("POST", "/v1/unknown"), ("GET", "/v1/view")):
                with self.subTest(
                    token_actor=self.tokens[token]["actor"], method=method, path=path
                ):
                    self.assertEqual(
                        self.request(path, {}, token=token, method=method),
                        (404, {"error": "Unknown endpoint"}),
                    )
        self.assertEqual(
            self.request("/v1/view", {"unexpected": True}),
            (400, {"error": "View request body must be empty"}),
        )
        self.assertEqual(self.store.records(), prior)
        self.assertEqual(self.request("/v1/view", {})[0], 200)
        self.assertEqual(self.request("/v1/view", {}, token="o" * 32)[0], 403)

    def test_fixed_port_restarts_immediately_but_cannot_be_shared_by_live_servers(
        self,
    ) -> None:
        port = self.server.port
        with self.assertRaises(OSError):
            self.serve(port=port)
        # Read through the server's FIN before closing the client. This puts
        # the listening endpoint's closed connection into real TCP TIME_WAIT.
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            client.sendall(b"GET /health/live HTTP/1.0\r\n\r\n")
            received = bytearray()
            while chunk := client.recv(4096):
                received.extend(chunk)
        self.assertIn(b"200 OK", received)
        self.server.close()
        replacement = self.serve(port=port)
        self.assertEqual(
            self.request("/health/live", method="GET", server=replacement),
            (200, {"live": True}),
        )
        with self.assertRaises(OSError):
            self.serve(port=port)

    def test_public_bind_requires_tls_and_https_client_checks_the_certificate(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            self.serve(host="0.0.0.0")
        secure = self.serve(tls_certificate=str(self.cert), tls_key=str(self.key))
        with self.assertRaises(TransportError):
            HTTPRoom(secure.url, "u" * 32, timeout=3)
        client = HTTPRoom(secure.url, "u" * 32, ca_file=str(self.cert))
        self.assertEqual(
            client.publish(self.question(), "user", 0)["status"], "accepted"
        )

    def test_client_refuses_cleartext_remote_hosts_before_connecting(self) -> None:
        with self.assertRaisesRegex(TransportError, "TLS"):
            HTTPRoom("http://192.0.2.20:1234", "u" * 32)

    def test_health_and_metrics_do_not_expose_room_payloads_or_bearer_tokens(
        self,
    ) -> None:
        self.request("/v1/proposals", self.question())
        self.assertEqual(
            self.request("/health/live", method="GET", token="wrong"),
            (200, {"live": True}),
        )
        self.assertEqual(self.request("/metrics", method="GET", token="a" * 32)[0], 403)
        code, metrics = self.request("/metrics", method="GET", token="o" * 32)
        self.assertEqual(code, 200)
        self.assertEqual(metrics["backend"]["operations"], 1)
        encoded = json.dumps(metrics)
        self.assertNotIn('"ping"', encoded)
        self.assertNotIn("u" * 32, encoded)
        self.assertNotIn("checkpoint", encoded)

    def test_request_capacity_and_duplicate_json_keys_are_rejected_before_dispatch(
        self,
    ) -> None:
        self.assertEqual(
            self.request("/v1/proposals", {"padding": "x" * 70_000})[0], 413
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.port, timeout=3
        )
        try:
            connection.request(
                "POST",
                "/v1/proposals",
                body=b'{"id":"q1","id":"q2"}',
                headers={
                    "Authorization": "Bearer " + "u" * 32,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()

    def test_slow_headers_have_an_absolute_deadline_and_cannot_hold_server_capacity(
        self,
    ) -> None:
        clock = TimerClock()
        with patch("remote_agent.network.threading.Timer", clock.timer):
            server = self.serve(read_timeout=10, max_connections=1)
            with socket.create_connection(("127.0.0.1", server.port)) as connection:
                connection.sendall(b"GET /health/live HTTP/1.1\r\nX-Slow: ")
                deadline = clock.await_timer()
                self.assertFalse(server.server.slots.acquire(blocking=False))
                connection.sendall(b"x")
                clock.advance(9)
                self.assertFalse(deadline.fired)
                connection.sendall(b"x")
                clock.advance(1)
                self.assertTrue(deadline.fired)
                await_capacity(server.server.slots)
                self.assertEqual(
                    self.request("/health/live", method="GET", server=server)[0], 200
                )

    def test_readiness_fails_after_engine_death_without_exposing_private_state(
        self,
    ) -> None:
        self.engine.close(force=True)
        code, data = self.request("/health/ready", method="GET")
        self.assertEqual((code, data), (503, {"ready": False}))

    @pytest.mark.contract_isolation
    def test_remote_agent_uses_network_authority_for_program_then_explicit_llm_takeover(
        self,
    ) -> None:
        directory = Path(self.folder.name)
        store = Store(directory / "agent.sqlite3")
        self.addCleanup(store.close)
        artifact = Artifact.publish(SOURCE, directory / "artifacts")
        checker = EngineProcess(
            CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        )
        self.addCleanup(checker.close)
        provider = ScriptedModel()
        agent = HybridAgent(
            store,
            Program(artifact),
            HTTPRoom(self.server.url, "a" * 32),
            LocalGate(checker),
            provider,
            actor="agent",
            artifact_directory=directory / "artifacts",
            control_actors={"user": ["takeover"]},
        )
        self.addCleanup(agent.close)
        assert isinstance(agent.room, HTTPRoom)

        def notify(event: Any) -> dict[str, Any]:
            agent.room.accepted_input(event, agent.actor, 0)
            return {"status": "notified"}

        service = HTTPService(
            AgentAPI(agent, notify=notify),
            {
                "r" * 32: {"actor": "room", "permissions": ["events"]},
                "c" * 32: {"actor": "user", "permissions": ["controls"]},
            },
        )
        self.addCleanup(service.close)
        client = HTTPRoom(self.server.url, "u" * 32)
        event = client.publish(self.question(), "user")
        code, result = self.request("/v1/events", event, token="r" * 32, server=service)
        self.assertEqual(
            (code, result, provider.calls), (200, {"status": "notified"}, [])
        )
        self.assertIsNone(store.get("input:" + event["id"]))
        page = agent.room.inbox(0)
        self.assertEqual(agent.receive(page["events"][0])["producer"], "program")
        cursor = page["cursor"]
        self.assertEqual(
            self.request("/v1/events", event, token="c" * 32, server=service)[0], 403
        )
        command = {
            "id": "take",
            "action": "takeover",
            "reason": "Review answer quality",
        }
        self.assertEqual(
            self.request("/v1/controls", command, token="c" * 32, server=service)[0],
            200,
        )
        event = client.publish(
            dict(self.question(), id="q2", expected_version=2), "user"
        )
        code, result = self.request("/v1/events", event, token="r" * 32, server=service)
        self.assertEqual(
            (code, result, provider.calls), (200, {"status": "notified"}, ["wake"])
        )
        page = agent.room.inbox(cursor)
        self.assertEqual(agent.receive(page["events"][0])["producer"], "llm")
        self.assertEqual(provider.calls, ["wake", "respond"])

    @pytest.mark.contract_isolation
    def test_event_ingress_classifies_verification_failures_and_self_authored_noop(
        self,
    ) -> None:
        directory = Path(self.folder.name)
        store = Store(directory / "agent.sqlite3")
        self.addCleanup(store.close)
        artifact = Artifact.publish(SOURCE, directory / "artifacts")
        checker = EngineProcess(
            CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        )
        self.addCleanup(checker.close)
        provider = ScriptedModel()
        agent = HybridAgent(
            store,
            Program(artifact),
            HTTPRoom(self.server.url, "a" * 32),
            LocalGate(checker),
            provider,
            actor="agent",
            artifact_directory=directory / "artifacts",
        )
        self.addCleanup(agent.close)
        assert isinstance(agent.room, HTTPRoom)

        # An explicit notification consumer using the shared authoritative verifier.
        def notify(data: Any) -> Any:
            event = agent.room.accepted_input(data, agent.actor, 0)
            return (
                {"status": "ignored", "reason": "self_authored"}
                if event["actor"] == agent.actor
                else {"status": "notified"}
            )

        network = HTTPService(
            AgentAPI(agent, notify=notify),
            {"r" * 32: {"actor": "room", "permissions": ["events"]}},
        )
        reference = Endpoint(
            agent, {"r" * 32: {"actor": "room", "events": True}}, notify=notify
        )
        self.addCleanup(network.close)
        self.addCleanup(reference.close)

        def post(server: Any, path: Any, data: Any) -> tuple[Any, ...]:
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server.server_address[1], timeout=3
            )
            try:
                connection.request(
                    "POST",
                    path,
                    json.dumps(data).encode(),
                    headers={
                        "Authorization": "Bearer " + "r" * 32,
                        "Content-Type": "application/json",
                    },
                )
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        event = self.room.publish(self.question(), "user")
        prior = store.records()
        for server, path in ((network, "/v1/events"), (reference, "/events")):
            for body, expected in (
                ({}, 400),
                (dict(event, id=[]), 400),
                (dict(event, id="forged"), 409),
                (dict(event, session="wrong"), 409),
                (dict(event, actor="agent"), 409),
                (dict(event, payload={"text": "changed"}), 409),
                (event, 200),
            ):
                with self.subTest(path=path, body=body, expected=expected):
                    self.assertEqual(post(server, path, body)[0], expected)
                    self.assertEqual(store.records(), prior)
                    self.assertEqual(provider.calls, [])
            for failure in (
                TransportError("view unavailable"),
                StoreError("storage unavailable"),
            ):
                with self.subTest(path=path, failure=type(failure).__name__):
                    with patch.object(agent.room, "view", side_effect=failure):
                        self.assertEqual(post(server, path, event)[0], 503)
                    self.assertEqual(store.records(), prior)
        agent.receive(event)  # Ordered consumer, not an HTTP callback.
        fact = next(
            f
            for f in agent.room.view("agent")["view"]["facts"].values()
            if f["actor"] == "agent"
        )
        own = dict(fact, session="s")
        prior = store.records()
        for server, path in ((network, "/v1/events"), (reference, "/events")):
            with self.subTest(path=path, case="verified_self_authored"):
                self.assertEqual(
                    post(server, path, own),
                    (200, {"status": "ignored", "reason": "self_authored"}),
                )
                self.assertEqual(store.records(), prior)
            with self.subTest(path=path, case="forged_self_authored"):
                self.assertEqual(
                    post(server, path, dict(own, payload={"text": "forged"}))[0], 409
                )
                self.assertEqual(store.records(), prior)
        with self.subTest(case="controller_verified_self_authored"):
            self.assertEqual(
                agent.receive(own), {"status": "ignored", "reason": "self_authored"}
            )
            self.assertEqual(store.records(), prior)
            self.assertEqual(store.get("queue"), [])
            self.assertEqual(provider.calls, [])

    def test_client_deadline_covers_a_server_that_never_finishes_its_headers(
        self,
    ) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        release = threading.Event()
        headers_sent = threading.Event()

        def stall() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                connection.recv(4096)
                connection.sendall(b"HTTP/1.1 200 OK\r\nX-Unfinished: ")
                headers_sent.set()
                if not release.wait(5):
                    raise AssertionError("The stalled HTTP peer was not released")

        def ready() -> None:
            self.assertTrue(headers_sent.wait(5))

        with ThreadPoolExecutor(max_workers=1) as executor:
            server = executor.submit(stall)
            try:
                with (
                    command_deadline(ready) as deadline,
                    self.assertRaises(TransportError),
                ):
                    HTTPRoom(
                        f"http://127.0.0.1:{listener.getsockname()[1]}",
                        "u" * 32,
                        timeout=10,
                    )
                self.assertTrue(deadline.expired)
                self.assertFalse(server.done())
            finally:
                release.set()
                listener.close()
                server.result(timeout=5)
