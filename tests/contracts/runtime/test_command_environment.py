from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from remote_agent.commands import call, capture, result_from_capture
from tests.contracts.runtime.commandenvironment import (
    CommandEnvironmentFixture,
    EnvironmentOptions,
)


@pytest.mark.contract_linux
class TestCommandEnvironment(CommandEnvironmentFixture):
    def test_capture_defaults_and_explicit_none_use_minimal_environment(self) -> None:
        cases: tuple[EnvironmentOptions, ...] = ({}, {"env": None})
        for kwargs in cases:
            with (
                self.subTest(kwargs=kwargs),
                patch.dict(os.environ, self.parent, clear=True),
            ):
                result = result_from_capture(capture(self.probe, {}, **kwargs))
                self.assertEqual(set(result["keys"]), {"PATH", "LANG", "TZ"})
                self.assertEqual(result["path"], "/usr/bin:/bin")
                self.assertEqual(result["tz"], "UTC")

    def test_call_defaults_and_explicit_none_use_minimal_environment(self) -> None:
        cases: tuple[EnvironmentOptions, ...] = ({}, {"env": None})
        for kwargs in cases:
            with (
                self.subTest(kwargs=kwargs),
                patch.dict(os.environ, self.parent, clear=True),
            ):
                result = call(self.probe, {}, **kwargs)
                self.assertEqual(set(result["keys"]), {"PATH", "LANG", "TZ"})
                self.assertEqual(result["tz"], "UTC")

    def test_explicit_environment_is_preserved_without_ambient_keys(self) -> None:
        environment = {
            "LANG": "C.UTF-8",
            "EXPLICIT_ALLOWED": "synthetic-approved-value",
        }
        with patch.dict(os.environ, self.parent, clear=True):
            result = call(self.probe, {}, env=environment)
        self.assertEqual(set(result["keys"]), set(environment))
        self.assertIsNone(result["path"])
        self.assertIsNone(result["tz"])
        self.assertEqual(
            environment,
            {"LANG": "C.UTF-8", "EXPLICIT_ALLOWED": "synthetic-approved-value"},
        )

    def test_explicit_empty_environment_is_not_replaced_with_defaults(self) -> None:
        # env prints no values when it receives the requested empty environment.
        with patch.dict(os.environ, self.parent, clear=True):
            result = capture(["/usr/bin/env"], {}, env={})
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["complete"])
        self.assertIsNone(result["failure"])
        self.assertEqual(result["stdout"], "")
