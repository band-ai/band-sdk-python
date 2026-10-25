"""A turn's originals keep their own identity; a singleton turn is the default."""

from __future__ import annotations

from dataclasses import replace

from tests.framework_conformance.invocationkit import turn_input
from tests.runtime.conftest import platform_msg


class TestAgentInput:
    def test_a_turn_without_explicit_originals_is_the_singleton_of_its_message(
        self,
    ) -> None:
        only = platform_msg("m1")

        assert replace(turn_input(only), messages=()).messages == (only,)
