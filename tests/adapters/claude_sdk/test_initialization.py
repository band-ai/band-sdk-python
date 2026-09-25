from __future__ import annotations

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.types import Capability


class TestInitialization:
    """Tests for adapter initialization (memory tools specific)."""

    def test_default_initialization(self):
        """Should initialize with no memory capability by default."""
        adapter = ClaudeSDKAdapter()
        assert Capability.MEMORY not in adapter.features.capabilities

    def test_enable_memory_tools(self):
        """Should accept capabilities=Capability.MEMORY."""
        adapter = ClaudeSDKAdapter(capabilities=Capability.MEMORY)
        assert Capability.MEMORY in adapter.features.capabilities
