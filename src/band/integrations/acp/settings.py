"""Validated configuration for the ``band-acp`` CLI."""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import ConfigDict, SecretStr, ValidationError, field_validator
from pydantic_settings import SettingsConfigDict

from band.core.bases import BandSettings, FrozenModel


class CliLogLevel(StrEnum):
    """Log levels accepted by ``band-acp --log-level``."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class AcpEnvSettings(BandSettings):
    """Environment defaults for ``band-acp`` (``BAND_*``).

    CLI flags override these. Empty env vars are ignored so a blank
    ``BAND_AGENT_ID=`` does not shadow a CLI flag or fail validation early.
    """

    model_config = SettingsConfigDict(env_prefix="BAND_")

    agent_id: str | None = None
    api_key: SecretStr | None = None
    rest_url: str = "https://app.band.ai"
    ws_url: str = "wss://app.band.ai/api/v1/socket/websocket"


class AcpCliConfig(FrozenModel):
    """Fully validated ``band-acp`` runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    api_key: SecretStr
    rest_url: str
    ws_url: str
    log_level: CliLogLevel | None = None

    @field_validator("agent_id", "rest_url", "ws_url", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("agent_id")
    @classmethod
    def _require_agent_id(cls, value: str) -> str:
        if not value:
            raise ValueError(
                "Agent ID is required. Use --agent-id or set BAND_AGENT_ID."
            )
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _require_api_key(cls, value: object) -> object:
        if value is None:
            raise ValueError("API key is required. Use --api-key or set BAND_API_KEY.")
        if isinstance(value, SecretStr):
            if not value.get_secret_value().strip():
                raise ValueError(
                    "API key is required. Use --api-key or set BAND_API_KEY."
                )
            return value
        text = str(value).strip()
        if not text:
            raise ValueError("API key is required. Use --api-key or set BAND_API_KEY.")
        return text

    @field_validator("rest_url")
    @classmethod
    def _http_rest_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"rest_url must be an http(s) URL with a host, got {value!r}"
            )
        return value.rstrip("/")

    @field_validator("ws_url")
    @classmethod
    def _websocket_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in ("ws", "wss") or not parsed.netloc:
            raise ValueError(f"ws_url must be a ws(s) URL with a host, got {value!r}")
        return value

    @classmethod
    def from_cli(
        cls,
        *,
        agent_id: str | None = None,
        api_key: str | None = None,
        rest_url: str | None = None,
        ws_url: str | None = None,
        log_level: str | CliLogLevel | None = None,
        env: AcpEnvSettings | None = None,
    ) -> AcpCliConfig:
        """Merge CLI overrides over :class:`AcpEnvSettings` and validate."""
        settings = env if env is not None else AcpEnvSettings()
        resolved_key: str | SecretStr | None
        if api_key is not None:
            resolved_key = api_key
        elif settings.api_key is not None:
            resolved_key = settings.api_key
        else:
            resolved_key = None

        level: CliLogLevel | None
        if log_level is None:
            level = None
        elif isinstance(log_level, CliLogLevel):
            level = log_level
        else:
            try:
                level = CliLogLevel(str(log_level).upper())
            except ValueError as exc:
                allowed = ", ".join(member.value for member in CliLogLevel)
                raise ValueError(
                    f"log_level must be one of {allowed}, got {log_level!r}"
                ) from exc

        try:
            return cls(
                agent_id=agent_id if agent_id is not None else settings.agent_id or "",
                api_key=resolved_key if resolved_key is not None else "",
                rest_url=rest_url if rest_url is not None else settings.rest_url,
                ws_url=ws_url if ws_url is not None else settings.ws_url,
                log_level=level,
            )
        except ValidationError as exc:
            for err in exc.errors():
                msg = err.get("msg")
                if isinstance(msg, str) and msg:
                    raise ValueError(msg.removeprefix("Value error, ")) from exc
            raise ValueError(str(exc)) from exc

    @property
    def api_key_value(self) -> str:
        """Plaintext API key for SDK constructors that take ``str``."""
        return self.api_key.get_secret_value()
