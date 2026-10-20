from __future__ import annotations

from band.runtime.sessionconfig import SessionConfig


class TestSessionConfigDefaults:
    """Verify SessionConfig default values."""

    def test_default_enable_context_hydration_is_true(self):
        """Default should enable context hydration for backward compatibility."""
        config = SessionConfig()
        assert config.enable_context_hydration is True

    def test_default_enable_context_cache_is_true(self):
        """Default should enable context caching."""
        config = SessionConfig()
        assert config.enable_context_cache is True

    def test_can_disable_context_hydration(self):
        """Should be able to explicitly disable context hydration."""
        config = SessionConfig(enable_context_hydration=False)
        assert config.enable_context_hydration is False
