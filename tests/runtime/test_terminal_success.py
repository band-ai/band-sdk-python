"""Unit tests for the shared "terminal productive work" predicate.

``is_terminal_success`` is the single source of truth the crewai / pydantic-ai
adapters use to decide whether an empty final model response is *benign* (the
agent already did its work) or a genuine no-response failure. The fail-loud
policy: read-only Band tools and *undeclared* custom tools never count; a custom
tool must opt in via ``band_terminal`` (checked by ``is_marked_terminal``).
"""

from __future__ import annotations

from band.runtime.custom_tools import is_marked_terminal
from band.runtime.tools import (
    ALL_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    is_terminal_success,
)

# A concrete Band tool of each kind, derived from the registry so a rename can't
# rot the test into asserting nothing.
_TERMINAL_BAND_TOOL = "band_send_message"
_READ_ONLY_BAND_TOOL = "band_get_memory"


def test_band_terminal_tool_success_counts() -> None:
    assert _TERMINAL_BAND_TOOL in ALL_TOOL_NAMES - READ_ONLY_TOOL_NAMES
    assert is_terminal_success(_TERMINAL_BAND_TOOL, succeeded=True) is True


def test_band_read_only_tool_never_counts() -> None:
    assert _READ_ONLY_BAND_TOOL in READ_ONLY_TOOL_NAMES
    assert is_terminal_success(_READ_ONLY_BAND_TOOL, succeeded=True) is False


def test_failed_tool_never_counts() -> None:
    assert is_terminal_success(_TERMINAL_BAND_TOOL, succeeded=False) is False


def test_undeclared_custom_tool_is_not_terminal() -> None:
    # A custom tool name is in neither set → fail-loud default (not terminal).
    assert is_terminal_success("weather", succeeded=True) is False
    assert (
        is_terminal_success("weather", succeeded=True, custom_terminal=False) is False
    )


def test_opted_in_custom_tool_is_terminal() -> None:
    assert (
        is_terminal_success("post_to_slack", succeeded=True, custom_terminal=True)
        is True
    )


def test_opted_in_custom_tool_still_needs_success() -> None:
    assert (
        is_terminal_success("post_to_slack", succeeded=False, custom_terminal=True)
        is False
    )


def test_is_marked_terminal_reads_the_flag() -> None:
    def plain() -> None: ...

    def terminal() -> None: ...

    terminal.band_terminal = True  # type: ignore[attr-defined]

    assert is_marked_terminal(plain) is False
    assert is_marked_terminal(terminal) is True

    class TerminalModel:
        band_terminal = True

    class PlainModel:
        pass

    assert is_marked_terminal(TerminalModel) is True
    assert is_marked_terminal(PlainModel) is False
