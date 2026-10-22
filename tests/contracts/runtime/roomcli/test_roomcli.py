from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import Mock, patch

import pytest

from remote_agent import room_cli
from tests.contracts.runtime.roomcli.kit import RoomCLIFixture


@pytest.mark.contract_runtime
class TestRoomCLI(RoomCLIFixture):
    def test_identity_definition_and_compact_view_preserve_legal_moves(self) -> None:
        self.assertEqual(self.success("identity")["body"]["actor"], "user")
        self.assertEqual(
            self.success("definition")["body"]["definition"], {"rules": ["question"]}
        )
        view = self.success("view", "--compact")["body"]["view"]
        self.assertNotIn("definition", view)
        self.assertEqual(view["legal"], [{"kind": "question"}])
        self.assertEqual(
            [raw for method, path, raw in self.requests if path == "/v1/view"], [b"{}"]
        )

    def test_random_id_and_byte_exact_retry_survive_separate_invocations(self) -> None:
        first = self.success(
            "propose", "question", '{"text":"ping"}', "--expected-version", "0"
        )
        self.assertEqual(first["body"]["status"], "accepted")
        self.assertRegex(first["body"]["id"], r"^[0-9a-f]{32}$")
        retry = self.success("propose", "--retry")
        self.assertEqual(retry["body"]["status"], "duplicate")
        self.assertEqual(self.proposals[0], self.proposals[1])
        self.assertEqual(json.loads(self.proposals[0])["expected_version"], 0)
        for path in (self.root / "state").rglob("*"):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(self.token.encode(), path.read_bytes())

    def test_ambiguous_network_failure_keeps_the_exact_staged_message(self) -> None:
        self.disconnect = True
        failed = self.cli("propose", "question", '{"text":"lost reply"}')
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(
            self.success("propose", "--retry")["body"]["status"], "duplicate"
        )
        self.assertEqual(self.proposals[0], self.proposals[1])

    def test_retry_cannot_change_payload_kind_id_or_version(self) -> None:
        self.success("propose", "question", '{"text":"original"}')
        for args in (
            ("question", '{"text":"changed"}'),
            ("--id", "new"),
            ("--expected-version", "1"),
        ):
            result = self.cli("propose", "--retry", *args)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.proposals), 1)

    def test_retry_without_staged_message_is_refused(self) -> None:
        result = self.cli("propose", "--retry")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No staged proposal for this endpoint and actor", result.stderr)
        self.assertEqual(self.proposals, [])

    def test_stage_and_cursor_are_endpoint_and_actor_scoped(self) -> None:
        self.success("propose", "question", '{"text":"one"}')
        self.success("inbox")
        alternate = self.serve()
        alternate_url = "http://127.0.0.1:" + str(alternate.server_port)
        self.assertNotEqual(
            self.cli("propose", "--retry", url=alternate_url).returncode, 0
        )
        self.success("inbox", url=alternate_url)
        self.token_file.write_text("a" * 32)
        self.assertNotEqual(self.cli("propose", "--retry").returncode, 0)
        self.success("inbox")
        self.assertEqual([page["after"] for page in self.polls], [0, 0, 0])

    def test_inbox_saved_cursor_advances_only_on_success(self) -> None:
        first = self.success("inbox", "--limit", "3")
        self.assertEqual(first["body"]["cursor"], 7)
        self.success("inbox", "--ack", "7")
        self.inbox_failure = True
        self.assertNotEqual(self.cli("inbox").returncode, 0)
        self.inbox_failure = False
        self.assertEqual(self.success("inbox")["body"]["events"], [])
        self.assertEqual([page["after"] for page in self.polls], [0, 7, 7])

    def test_df45_peek_replays_after_parse_failure_and_restart_until_ack(self) -> None:
        first = self.success("inbox", "--peek")["body"]
        self.assertEqual(first["events"], [{"id": "event"}])
        # Each invocation is a fresh process. A caller that cannot parse/process
        # the page simply does not ack; the next process must see it again.
        self.assertEqual(self.success("inbox", "--peek")["body"], first)
        self.assertEqual(self.success("inbox")["body"], first)
        self.success("inbox", "--ack", str(first["cursor"]))
        self.assertEqual(self.success("inbox", "--peek")["body"]["events"], [])
        self.assertEqual([page["after"] for page in self.polls], [0, 0, 0, 7])

    def test_df45_default_read_does_not_acknowledge_processing(self) -> None:
        first = self.success("inbox")["body"]
        self.assertEqual(self.success("inbox")["body"], first)
        self.assertEqual([page["after"] for page in self.polls], [0, 0])

    def test_df45_ack_requires_exact_delivered_cursor_and_is_idempotent(self) -> None:
        unseen = self.cli("inbox", "--ack", "7")
        self.assertNotEqual(unseen.returncode, 0)
        self.assertIn("Cursor was not delivered", unseen.stderr)
        self.success("inbox", "--peek")
        for cursor in ("-1", "1", "8", "9007199254740992"):
            refused = self.cli("inbox", "--ack", cursor)
            self.assertNotEqual(refused.returncode, 0, cursor)
            self.assertIn("cursor", refused.stderr.lower())
        self.success("inbox", "--ack", "7")
        self.success("inbox", "--ack", "7")
        self.assertEqual(self.success("inbox")["body"]["events"], [])
        self.assertEqual([page["after"] for page in self.polls], [0, 7])

    def test_df45_failed_output_does_not_offer_a_cursor_for_ack(self) -> None:
        directory = self.root / "private"
        directory.mkdir(mode=0o700)
        scope = {"actor": "user"}
        client = Mock(token=self.token)
        client.inbox.return_value = {"cursor": 7, "events": [{"id": "event"}]}
        args = Namespace(limit=16, peek=True, ack=None)
        with (
            patch.object(room_cli, "emit", side_effect=BrokenPipeError),
            self.assertRaises(BrokenPipeError),
        ):
            room_cli.inbox(args, client, directory, scope)
        args.ack = 7
        with self.assertRaisesRegex(room_cli.ClientError, "Cursor was not delivered"):
            room_cli.inbox(args, client, directory, scope)

    def test_rejections_are_nonzero_and_new_proposal_gets_a_new_id(self) -> None:
        rejected = self.cli("propose", "reject", "{}")
        self.assertNotEqual(rejected.returncode, 0)
        rejected_id = json.loads(rejected.stdout)["body"]["id"]
        accepted = self.success("propose", "question", "{}")
        self.assertNotEqual(rejected_id, accepted["body"]["id"])

    def test_df44_byte_and_grapheme_overflow_is_local_and_reports_counts(self) -> None:
        self.text_definition(byte_max=4)
        self.success("propose", "question", json.dumps({"text": "😀"}))
        for text, constraint, actual in (("😀😀", "bytes", 8), ("ab", "graphemes", 2)):
            with self.subTest(text=text):
                before = len(self.proposals)
                result = self.cli("propose", "question", json.dumps({"text": text}))
                self.assertEqual(result.returncode, 1, result.stdout)
                detail = json.loads(result.stderr)
                self.assertEqual(detail["field"], "text")
                self.assertEqual(detail["constraint"], constraint)
                self.assertEqual(detail["actual"], actual)
                self.assertEqual(detail["max"], 4 if constraint == "bytes" else 1)
                self.assertEqual(len(self.proposals), before)
        self.assertEqual(len(list((self.root / "state").rglob("staged.json"))), 1)

    def test_df44_unicode_clusters_and_minimum_boundaries(self) -> None:
        self.text_definition(byte_min=1, byte_max=64, grapheme_min=1)
        for text in ("e\u0301", "\r\n", "🇦🇪", "👩‍👩‍👧‍👦", "क्\u200dक"):
            with self.subTest(text=text):
                self.success("propose", "question", json.dumps({"text": text}))
        self.assertEqual(len(self.proposals), 5)
        result = self.cli("propose", "question", '{"text":""}')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(json.loads(result.stderr)["actual"], 0)
        self.assertEqual(json.loads(result.stderr)["min"], 1)
        self.assertEqual(len(self.proposals), 5)

    def test_df44_definition_is_bound_to_authenticated_identity(self) -> None:
        self.text_definition()
        self.definition_identity = {"digest": "other"}
        result = self.cli("propose", "question", '{"text":"a"}')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("definition differs", result.stderr)
        self.assertEqual(self.proposals, [])

    def test_df44_preflight_does_not_stage_or_block_a_corrected_proposal(self) -> None:
        self.text_definition(byte_max=2)
        result = self.cli("propose", "question", '{"text":"abc"}')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(list((self.root / "state").rglob("staged.json")), [])
        self.success("propose", "question", '{"text":"é"}')

    def test_df31_illegal_message_includes_only_current_actor_legal_kinds(self) -> None:
        self.rejection_reason = "illegal_message"
        self.legal = [
            {"kind": "question", "meta": {"private": "do not echo"}},
            {"kind": "status"},
            {"kind": "question"},
        ]
        result = self.cli("propose", "reject", "{}")
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)["body"]
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["reason"], "illegal_message")
        self.assertEqual(body["detail"], {"unchanged": True})
        self.assertEqual(body["legal_kinds"], ["question", "status"])
        self.assertNotIn("do not echo", result.stdout + result.stderr)
        self.assertEqual(
            [raw for method, path, raw in self.requests if path == "/v1/view"], [b"{}"]
        )

    def test_df31_failed_hint_lookup_preserves_rejection_and_resolves_stage(
        self,
    ) -> None:
        self.rejection_reason = "illegal_message"
        self.view_failure = True
        result = self.cli("propose", "reject", "{}")
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)["body"]
        self.assertEqual(body["reason"], "illegal_message")
        self.assertTrue(body["legal_kinds_unavailable"])
        self.view_failure = False
        self.success("propose", "question", "{}")

    def test_df31_other_rejections_do_not_fetch_or_invent_legal_hints(self) -> None:
        self.rejection_reason = "payload"
        result = self.cli("propose", "reject", "{}")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("legal_kinds", json.loads(result.stdout)["body"])
        self.assertFalse(any(path == "/v1/view" for method, path, raw in self.requests))

    def test_payload_file_and_secret_safe_failure_output(self) -> None:
        payload = self.root / "payload.json"
        payload.write_text('{"text":"file"}')
        self.success("propose", "question", str(payload))
        self.echo_secret = True
        refused = self.cli("identity")
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn(self.token, refused.stdout + refused.stderr)

    def test_public_token_file_and_nonloopback_http_are_refused(self) -> None:
        cases = (
            (0o644, None, "Private files must be owned regular files with mode 0600"),
            (0o600, "http://192.0.2.1:9999", "Remote room connections require TLS"),
        )
        for mode, url, reason in cases:
            with self.subTest(mode=oct(mode), url=url):
                self.token_file.chmod(mode)
                result = self.cli("identity", url=url)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)
        self.assertEqual(self.requests, [])
