from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from band.contracts.programs import ProgramTask
from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.service import Service
from remote_agent.store import Store
from tests.contracts.runtime.taskroom import UnknownAckRoom


@pytest.mark.contract_linux
class TestGenericTask(unittest.TestCase):
    def test_counterparty_role_and_arbitrary_kind_retain_the_exact_unknown_ack_request(
        self,
    ) -> None:
        room = UnknownAckRoom("session")

        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory) / "journal") as store,
        ):
            driver = GenericRuntimeDriver.__new__(GenericRuntimeDriver)
            driver.task_rooms = {"requester": room}
            driver.service = cast(Service, SimpleNamespace(store=store))
            body = {
                "id": "same-input",
                "sender_role": "requester",
                "kind": "normalize",
                "payload": {"text": "Band"},
            }
            with self.assertRaisesRegex(ValueError, "lost acknowledgement"):
                driver.task(body)
            with self.assertRaisesRegex(ValueError, "effect_identity_conflict"):
                driver.task(dict(body, payload={"text": "Changed"}))
            self.assertEqual(driver.task(body)["decision"]["status"], "duplicate")
            self.assertEqual(driver.task(body)["decision"]["status"], "duplicate")
            self.assertEqual(len(room.requests), 2)
            self.assertEqual(room.requests[0], room.requests[1])
            self.assertEqual(room.actors, ["Agent:requester", "Agent:requester"])
            self.assertEqual(room.requests[0]["expected_version"], 0)
            self.assertEqual(room.actors[0], "Agent:requester")

    def test_task_cannot_choose_the_supervised_role_or_guess_between_counterparties(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory) / "journal") as store,
        ):
            driver = GenericRuntimeDriver.__new__(GenericRuntimeDriver)
            driver.task_rooms = {"requester": object(), "reviewer": object()}
            driver.service = cast(Service, SimpleNamespace(store=store))
            body = {"id": "new-input", "kind": "normalize", "payload": {"text": "Band"}}
            with self.assertRaisesRegex(ValueError, "sending_role_required"):
                driver.task(body)
            with self.assertRaisesRegex(ValueError, "sending_role_not_granted"):
                driver.task(dict(body, sender_role="normalizer"))
            self.assertFalse(store.records())

    def test_public_task_selects_one_of_two_roles_and_keeps_it_through_unknown_ack(
        self,
    ) -> None:
        requester = UnknownAckRoom("session")
        reviewer = UnknownAckRoom("session")
        reviewer.actor = "Agent:reviewer"
        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory) / "journal") as store,
        ):
            driver = GenericRuntimeDriver.__new__(GenericRuntimeDriver)
            driver.task_rooms = {"requester": requester, "reviewer": reviewer}
            driver.service = cast(Service, SimpleNamespace(store=store))
            task = ProgramTask(
                id="selected-input",
                sender_role="reviewer",
                kind="normalize",
                payload={"text": "Band"},
            )
            with self.assertRaisesRegex(ValueError, "lost acknowledgement"):
                driver.task(task.body())
            self.assertEqual(requester.requests, [])
            self.assertEqual(reviewer.actors, ["Agent:reviewer"])
            self.assertEqual(
                store.get("effect:selected-input")["request"]["sender_role"],
                "reviewer",
            )
            changed = ProgramTask(
                id="selected-input",
                sender_role="requester",
                kind="normalize",
                payload={"text": "Band"},
            )
            with self.assertRaisesRegex(ValueError, "effect_identity_conflict"):
                driver.task(changed.body())
            self.assertEqual(
                driver.task(task.body())["decision"]["status"], "duplicate"
            )
            self.assertEqual(
                driver.task(task.body())["decision"]["status"], "duplicate"
            )
            self.assertEqual(requester.requests, [])
            self.assertEqual(len(reviewer.requests), 2)
            self.assertEqual(reviewer.requests[0], reviewer.requests[1])
            self.assertEqual(reviewer.actors, ["Agent:reviewer", "Agent:reviewer"])
            self.assertEqual(reviewer.requests[0]["expected_version"], 0)
            self.assertEqual(reviewer.requests[0]["payload"], {"text": "Band"})
