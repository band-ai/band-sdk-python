"""Require static service identities and reject weakened host settings."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from contracts.deploy import qualifysystemd as qualification
from tests.contracts.release.units import (
    StartCaptured,
    mock_host_copy,
    sealed_fixture,
)
from tests.paths import CONTRACT_DEPLOY_ROOT


@pytest.mark.contract_linux
class TestStaticUnitQualification(unittest.TestCase):
    def test_driver_rejects_bad_effective_unit_before_reporting_success(self) -> None:
        """Mock host transport only: execute the driver's real acceptance path."""
        for directive, weakened in (
            ("NoNewPrivileges", "no"),
            ("ProtectSystem", "no"),
            ("ProtectHome", "no"),
            ("PrivateTmp", "no"),
            ("PrivateDevices", "no"),
            ("RestrictSUIDSGID", "no"),
        ):
            with (
                self.subTest(directive=directive),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                release = root / "release"
                (release / "examples").mkdir(parents=True)
                (release / "examples/partial_program.py").write_text("# fixture\n")
                shutil.copytree(
                    CONTRACT_DEPLOY_ROOT / "systemd",
                    release / "contracts/deploy/systemd",
                )
                sealed_fixture(release)

                def run(
                    argv: list[str | Path],
                    *,
                    directive: str = directive,
                    weakened: str = weakened,
                    **kwargs: Any,
                ) -> subprocess.CompletedProcess[str]:
                    command = list(map(str, argv))
                    mock_host_copy(command)
                    if command[:2] == ["systemctl", "show"]:
                        values = dict(
                            qualification.required_protections("room"),
                            MainPID="123",
                            ControlGroup="/fixture",
                        )
                        values[directive] = weakened
                        return subprocess.CompletedProcess(
                            command,
                            0,
                            "\n".join(
                                f"{key}={value}" for key, value in values.items()
                            ),
                            "",
                        )
                    return subprocess.CompletedProcess(
                        command, 1 if command[0] == "test" else 0, "systemd 255\n", ""
                    )

                with (
                    patch.object(
                        sys,
                        "argv",
                        [
                            "qualifysystemd",
                            "--release",
                            str(release),
                            "--state",
                            str(root / "state"),
                        ],
                    ),
                    patch.object(qualification, "run", side_effect=run),
                    patch.object(qualification, "request", return_value=(200, {})),
                    patch.object(
                        qualification,
                        "Path",
                        side_effect=lambda *parts, root=root: (
                            (root / "opt") if parts == ("/opt",) else Path(*parts)
                        ),
                    ),
                    patch.object(
                        qualification.pwd,
                        "getpwnam",
                        side_effect=lambda name: SimpleNamespace(
                            pw_uid=2101 if name == "contract-room" else 2102
                        ),
                    ),
                    patch.object(
                        qualification.grp,
                        "getgrnam",
                        return_value=SimpleNamespace(gr_gid=2101),
                    ),
                    self.assertRaisesRegex(AssertionError, directive),
                ):
                    qualification.main()
                self.assertFalse((root / "state/result.json").exists())

    def test_transient_unit_uses_shipped_static_identity_without_dynamic_user(
        self,
    ) -> None:
        commands = []

        def run(
            argv: list[str | Path], **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            command = list(map(str, argv))
            commands.append(command)
            mock_host_copy(command)
            if command[0] == "systemd-run":
                raise StartCaptured
            return subprocess.CompletedProcess(command, 0, "systemd 255\n", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            (release / "examples").mkdir(parents=True)
            (release / "examples/partial_program.py").write_text("# fixture\n")
            shutil.copytree(
                CONTRACT_DEPLOY_ROOT / "systemd", release / "contracts/deploy/systemd"
            )
            sealed_fixture(release)
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "qualifysystemd",
                        "--release",
                        str(release),
                        "--state",
                        str(root / "state"),
                    ],
                ),
                patch.object(qualification, "run", side_effect=run),
                patch.object(
                    qualification,
                    "Path",
                    side_effect=lambda *parts, root=root: (
                        (root / "opt") if parts == ("/opt",) else Path(*parts)
                    ),
                ),
                patch.object(
                    qualification.pwd,
                    "getpwnam",
                    side_effect=lambda name: SimpleNamespace(
                        pw_uid=2101 if name == "contract-room" else 2102
                    ),
                ),
                patch.object(
                    qualification.grp,
                    "getgrnam",
                    side_effect=lambda name: SimpleNamespace(gr_gid=2101),
                ),
                self.assertRaises(StartCaptured),
            ):
                qualification.main()
        command = next(command for command in commands if command[0] == "systemd-run")
        properties = dict(
            value.split("=", 1)
            for index, value in enumerate(command)
            if index and command[index - 1] == "--property"
        )
        self.assertNotEqual(properties.get("DynamicUser", "no"), "yes")
        self.assertEqual(properties["User"], "contract-room")
        self.assertEqual(properties["Group"], "contract-room")

    def test_absent_static_users_fail_without_creating_accounts(self) -> None:
        with (
            patch.object(
                qualification.pwd, "getpwnam", side_effect=KeyError("missing")
            ),
            self.assertRaisesRegex(RuntimeError, "pre-created static"),
        ):
            qualification.static_identities()

    def test_root_or_shared_static_users_are_rejected(self) -> None:
        for room, agent in ((0, 2102), (2101, 2101)):
            with (
                self.subTest(uids=(room, agent)),
                patch.object(
                    qualification.pwd,
                    "getpwnam",
                    side_effect=lambda name, room=room, agent=agent: SimpleNamespace(
                        pw_uid=room if name == "contract-room" else agent
                    ),
                ),
                patch.object(
                    qualification.grp,
                    "getgrnam",
                    return_value=SimpleNamespace(gr_gid=2101),
                ),
                self.assertRaisesRegex(RuntimeError, "distinct non-root"),
            ):
                qualification.static_identities()
