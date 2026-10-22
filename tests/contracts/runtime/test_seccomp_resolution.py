from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.program import Artifact, Program, ProgramError
from remote_agent.sandbox import SandboxError, command, policy, restrict
from tests.contracts.runtime.seccompresolution import SeccompResolutionFixture


class TestSeccompResolution(SeccompResolutionFixture):
    def test_unresolved_required_names_fail_before_limits_and_filter_load(self) -> None:
        for name in (
            b"clone3",
            b"socket",
            b"io_uring_setup",
            b"io_uring_enter",
            b"io_uring_register",
        ):
            with self.subTest(name=name):
                library = self.library(name, -1)  # __NR_SCMP_ERROR, not arch absence.
                with (
                    patch("remote_agent.sandbox.ctypes.CDLL", return_value=library),
                    self.linux_limits() as limits,
                    self.assertRaisesRegex(SandboxError, "resolve.*" + name.decode()),
                ):
                    restrict(policy(2))
                limits.assert_not_called()
                library.seccomp_load.assert_not_called()
                library.seccomp_release.assert_called_once_with(42)

    def test_arch_absent_single_syscall_pseudo_numbers_are_tolerated(self) -> None:
        library = self.library(b"kexec_file_load", -10001)
        with (
            patch("remote_agent.sandbox.ctypes.CDLL", return_value=library),
            self.linux_limits(),
        ):
            restrict(policy(2))
        self.assertNotIn(
            -10001, [call.args[2] for call in library.seccomp_rule_add.call_args_list]
        )
        library.seccomp_load.assert_called_once_with(42)
        library.seccomp_release.assert_called_once_with(42)

    def test_multiplexed_socket_pseudo_number_is_enforced_not_skipped(self) -> None:
        library = self.library(b"socket", -101)
        with (
            patch("remote_agent.sandbox.ctypes.CDLL", return_value=library),
            self.linux_limits(),
        ):
            restrict(policy(2))
        self.assertIn(
            -101, [call.args[2] for call in library.seccomp_rule_add.call_args_list]
        )
        library.seccomp_load.assert_called_once_with(42)

    def test_invalid_resolver_or_rejected_rule_stops_filter_load(self) -> None:
        for number in (-2, -101, 123):
            with self.subTest(number=number):
                library = self.library(b"socket", number)
                library.seccomp_rule_add.side_effect = (
                    lambda ctx, action, syscall, count, *, number=number: (
                        -22 if syscall == number else 0
                    )
                )
                with (
                    patch("remote_agent.sandbox.ctypes.CDLL", return_value=library),
                    self.linux_limits(),
                    self.assertRaisesRegex(SandboxError, "syscall rule: socket"),
                ):
                    restrict(policy(2))
                library.seccomp_load.assert_not_called()
                library.seccomp_release.assert_called_once_with(42)

    @pytest.mark.contract_isolation
    def test_real_isolated_worker_refuses_unknown_name_before_evaluating_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="seccomp-resolution-") as directory:
            artifact = Artifact.publish(
                "raise RuntimeError('SOURCE_WAS_EVALUATED')\n",
                Path(directory) / "artifacts",
            )
            argv, enforced = command(artifact, timeout=2)
            # Keep actual bwrap, real libseccomp, actual worker and resource policy.
            # Fault only the resolver's answer for one required name.
            argv[-1] = """
import ctypes
original_cdll = ctypes.CDLL
def faulted_cdll(*args, **kwargs):
    library = original_cdll(*args, **kwargs)
    resolve = library.seccomp_syscall_resolve_name
    resolve.argtypes = [ctypes.c_char_p]
    resolve.restype = ctypes.c_int
    def resolve_name(name):
        return -1 if name == b'io_uring_setup' else resolve(name)
    library.seccomp_syscall_resolve_name = resolve_name
    return library
ctypes.CDLL = faulted_cdll
""" + argv[-1]
            with (
                patch("remote_agent.program.command", return_value=(argv, enforced)),
                self.assertRaisesRegex(ProgramError, "resolve.*io_uring_setup"),
            ):
                Program(artifact).describe({})
