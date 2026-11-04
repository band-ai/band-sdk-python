"""DependencyContract at the released CrewAI tool boundary."""

from __future__ import annotations

from typing import Any

import pytest
from crewai.tools import ToolFailure

from tests.crewaicontract import ToolFailure as FailureContract


class TestDependencyContract:
    @pytest.mark.parametrize("code", [None, "denied"])
    def test_optional_dependency_double_matches_released_failure_text(
        self, code: Any
    ) -> None:
        native = ToolFailure(message="failure text", code=code)
        contract = FailureContract(message="failure text", code=code)
        assert contract.as_agent_message() == native.as_agent_message()
