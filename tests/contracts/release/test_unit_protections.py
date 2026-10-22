"""Observe the shipped unit policy and effective system-manager settings."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from contracts.deploy import qualifysystemd as qualification
from contracts.deploy.unitconfig import UnitParser
from tests.contracts.release.units import (
    ROOM_PROTECTIONS,
)
from tests.paths import CONTRACT_DEPLOY_ROOT


class TestRoomHardening(unittest.TestCase):
    def test_room_template_has_required_boundaries_without_restricting_agent(
        self,
    ) -> None:
        for kind in ("room", "agent"):
            parser = UnitParser(interpolation=None)
            parser.read(
                CONTRACT_DEPLOY_ROOT / "systemd" / ("contract-" + kind + ".service")
            )
            for key, value in ROOM_PROTECTIONS.items():
                with self.subTest(kind=kind, directive=key):
                    if kind == "room":
                        self.assertEqual(
                            parser["Service"].get(key),
                            "EPERM" if key == "SystemCallErrorNumber" else value,
                        )
                    else:
                        self.assertNotIn(key, parser["Service"])

    def test_effective_room_hardening_cannot_be_ignored(self) -> None:
        actual = dict(qualification.required_protections("room"), **ROOM_PROTECTIONS)
        with patch.object(
            qualification,
            "system_service_syscalls",
            return_value=frozenset({"read", "write", "execve"}),
        ):
            qualification.assert_effective_protections("room", actual)
            for key in ROOM_PROTECTIONS:
                with self.subTest(directive=key):
                    bad = dict(actual, **{key: "unsafe"})
                    with self.assertRaisesRegex(AssertionError, key):
                        qualification.assert_effective_protections("room", bad)

    def test_expanded_effective_syscalls_are_checked_not_just_filter_presence(
        self,
    ) -> None:
        actual = dict(qualification.required_protections("room"), **ROOM_PROTECTIONS)
        actual["SystemCallFilter"] = "write execve read"
        with patch.object(
            qualification,
            "system_service_syscalls",
            return_value={"read", "write", "execve"},
            create=True,
        ):
            qualification.assert_effective_protections("room", actual)
            for value in ("", "~mount", "read write execve mount"):
                with (
                    self.subTest(filter=value),
                    self.assertRaisesRegex(AssertionError, "SystemCallFilter"),
                ):
                    qualification.assert_effective_protections(
                        "room", dict(actual, SystemCallFilter=value)
                    )

    def test_systemd_syscall_groups_expand_recursively_without_duplicate_groups(
        self,
    ) -> None:
        groups = {
            "@system-service": "@system-service\n # comment\n @default\n @io\n read\n",
            "@default": "@default\n execve\n @io\n",
            "@io": "@io\n \x1b[0mwrite\n read\n",
        }
        qualification.system_service_syscalls.cache_clear()
        try:
            with patch.object(
                qualification,
                "run",
                side_effect=lambda argv: SimpleNamespace(stdout=groups[argv[-1]]),
            ) as run:
                self.assertEqual(
                    qualification.system_service_syscalls(), {"read", "write", "execve"}
                )
                self.assertEqual(run.call_count, 3)
        finally:
            qualification.system_service_syscalls.cache_clear()

    def test_missing_or_weakened_effective_directives_fail_qualification(self) -> None:
        with patch.object(
            qualification,
            "system_service_syscalls",
            return_value=frozenset({"read", "write", "execve"}),
        ):
            for kind in ("room", "agent"):
                required = qualification.required_protections(kind)
                qualification.assert_effective_protections(kind, required)
                for directive in required:
                    with self.subTest(
                        kind=kind, directive=directive, mutation="missing"
                    ):
                        bad = dict(required)
                        del bad[directive]
                        with self.assertRaisesRegex(AssertionError, directive):
                            qualification.assert_effective_protections(kind, bad)
                    with self.subTest(
                        kind=kind, directive=directive, mutation="weakened"
                    ):
                        bad = dict(required, **{directive: "unsafe"})
                        with self.assertRaisesRegex(AssertionError, directive):
                            qualification.assert_effective_protections(kind, bad)

    def test_address_family_order_is_not_security_relevant(self) -> None:
        required = qualification.required_protections("agent")
        required["RestrictAddressFamilies"] = "AF_INET6 AF_UNIX AF_NETLINK AF_INET"
        qualification.assert_effective_protections("agent", required)
