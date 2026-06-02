"""Base settings for integration tests.

Provides a base TestSettings class using Pydantic Settings for
loading test configuration from .env.test files.

Usage:
    from band_testing.settings import BaseTestSettings

    class MyTestSettings(BaseTestSettings):
        my_api_key: str = ""
        my_service_url: str = "http://localhost:8000"

    settings = MyTestSettings()
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseTestSettings(BaseSettings):
    """Base settings class for integration tests.

    Subclass this to create test settings for your project.
    Settings are loaded from .env.test file in the tests directory.

    Example:
        class TestSettings(BaseTestSettings):
            # Primary credentials
            band_api_key: str = ""
            test_agent_id: str = ""

            # Server URLs
            band_base_url: str = "http://localhost:4000"

        settings = TestSettings()
        if settings.band_api_key:
            client = create_client(settings.band_api_key)

    Attributes:
        model_config: Pydantic settings configuration.
    """

    # Subclasses should override this to point to their .env.test location
    _env_file_path: ClassVar[Path | None] = None

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, **kwargs: Any) -> None:
        """Initialize settings, looking for .env.test file."""
        # If subclass defines _env_file_path, use it
        if self._env_file_path:
            kwargs.setdefault("_env_file", self._env_file_path)
        super().__init__(**kwargs)


class BandTestSettings(BaseTestSettings):
    """Standard Band test settings for integration tests.

    This provides common settings used across Band repositories.
    Load from .env.test in your tests directory.

    Example .env.test:
        BAND_API_KEY=your-agent-api-key
        TEST_AGENT_ID=your-agent-uuid
        BAND_BASE_URL=https://app.band.ai
        BAND_WS_URL=wss://app.band.ai/api/v1/socket/websocket

    Usage:
        from band_testing.settings import BandTestSettings

        class TestSettings(BandTestSettings):
            _env_file_path = Path(__file__).parent / ".env.test"

        settings = TestSettings()
    """

    # Primary agent credentials
    band_api_key: str = ""
    test_agent_id: str = ""

    # Secondary agent credentials (for multi-agent tests)
    band_api_key_2: str = ""
    test_agent_id_2: str = ""

    # User API key (for user operations like registering agents)
    band_api_key_user: str = ""

    # Server URLs
    band_base_url: str = "http://localhost:4000"
    band_ws_url: str = "ws://localhost:4000/api/v1/socket/websocket"

    @property
    def has_api_key(self) -> bool:
        """Check if primary API key is configured."""
        return bool(self.band_api_key)

    @property
    def has_multi_agent(self) -> bool:
        """Check if both agent API keys are configured."""
        return bool(self.band_api_key and self.band_api_key_2)

    @property
    def has_user_api(self) -> bool:
        """Check if user API key is configured."""
        return bool(self.band_api_key_user)
