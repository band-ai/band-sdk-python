from __future__ import annotations

import unittest
from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch


class SeccompResolutionFixture(unittest.TestCase):
    @contextmanager
    def linux_limits(self) -> Generator[Mock, None, None]:
        limits = Mock()
        resource = SimpleNamespace(
            RLIMIT_AS=0,
            RLIMIT_CPU=1,
            RLIMIT_NPROC=2,
            RLIMIT_NOFILE=3,
            RLIMIT_FSIZE=4,
            RLIMIT_CORE=5,
            setrlimit=limits,
        )
        with (
            patch("remote_agent.sandbox.sys", SimpleNamespace(platform="linux")),
            patch("remote_agent.sandbox.resource", resource, create=True),
        ):
            yield limits

    def library(self, name: Any, number: Any) -> Any:
        library = Mock()
        library.seccomp_init.return_value = 42
        library.seccomp_syscall_resolve_name.side_effect = lambda item: (
            number if item == name else 100
        )
        library.seccomp_rule_add.return_value = 0
        library.seccomp_load.return_value = 0
        return library
