"""DeclarationCandidateDetection behavior of the Google ADK adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.adapters.googleadk.bridge import (
    _BandToolBridge,
    _get_tool_bridge_class,
    _google_adk_mod,
)


class TestDeclarationCandidateDetection:
    """Tests for _DECLARATION_CANDIDATES probing and smoke-test."""

    def test_no_candidates_raises_runtime_error(self):
        """Should raise RuntimeError when BaseTool has no known declaration method."""
        _get_tool_bridge_class.cache_clear()
        try:
            with patch.object(
                _google_adk_mod,
                "_DECLARATION_CANDIDATES",
                ("_nonexistent_method",),
            ):
                _get_tool_bridge_class.cache_clear()
                with pytest.raises(RuntimeError, match="no known declaration method"):
                    _get_tool_bridge_class()
        finally:
            _get_tool_bridge_class.cache_clear()
            # Re-populate cache with valid class
            _get_tool_bridge_class()

    def test_smoke_test_runs_at_class_creation(self):
        """Smoke-test should verify declaration mechanism end-to-end."""
        # The fact that _get_tool_bridge_class() returned successfully at
        # module level (line 26) proves the smoke-test passed.  Verify the
        # returned class has the expected declaration method.
        bridge = _BandToolBridge(
            tool_name="smoke",
            tool_description="smoke test",
            parameters_schema={},
            tools=MagicMock(),
            custom_tools=[],
        )
        decl = bridge._get_declaration()
        assert decl is not None
        assert decl.name == "smoke"
