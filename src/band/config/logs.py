"""Environment-driven logging configuration for Band SDK applications."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.logging_config import (
    FileStyle,
    FormatStyle,
    LogLevel,
    LoggingConfig,
    LoggingStyle,
    LogStream,
    build_logging_config,
    coerce_log_level_name,
    configure_logging,
)


def _lowercase_style(value: object) -> object:
    return value.lower() if isinstance(value, str) else value


class LogSettings(BaseSettings):
    """Validated ``BAND_LOG_*`` knobs for process-wide logging.

    Maps environment variables into :func:`band.logging_config.configure_logging`.
    Consumer-specific logger defaults are passed to :meth:`configure` /
    :meth:`build_config` and are overridden by ``BAND_LOG_OVERRIDES``.

    Two common setups:

    - **Library / embed** (default): ``LogSettings().configure()`` — Band logs at
      ``BAND_LOG_LEVEL``, other loggers stay quiet (root ``WARNING``).
    - **Application entrypoint**: ``LogSettings().for_application().configure()`` —
      your process's own loggers (``__main__``, runners, etc.) use the same level
      as Band. Call this when *this process* owns the logs.
    """

    model_config = SettingsConfigDict(
        env_prefix="BAND_",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    log_level: str = Field(
        "INFO",
        description="Level for the band logger (and console when a file sink differs).",
    )
    log_root_level: str = Field(
        "WARNING",
        description="Root logger level for non-Band loggers.",
    )
    log_file: Path | None = Field(
        default=None,
        description="Optional log file path. None disables file logging.",
    )
    log_file_level: str | None = Field(
        default=None,
        description="File handler level. Defaults to log_level when unset.",
    )
    log_max_bytes: int = Field(
        0,
        ge=0,
        description="Max bytes before rotation. 0 uses a plain FileHandler.",
    )
    log_backups: int = Field(
        0,
        ge=0,
        description="Rotated backup files to keep when log_max_bytes > 0.",
    )
    log_console_style: Annotated[LoggingStyle, BeforeValidator(_lowercase_style)] = (
        Field(
            LoggingStyle.STANDARD,
            description="Console formatter style: standard, rich, or json.",
        )
    )
    log_file_style: Annotated[FileStyle, BeforeValidator(_lowercase_style)] = Field(
        LoggingStyle.STANDARD,
        description="File formatter style: standard or json.",
    )
    log_stream: Annotated[LogStream, BeforeValidator(_lowercase_style)] = Field(
        LogStream.STDERR,
        description="Console stream: stderr or stdout.",
    )
    log_overrides: dict[str, str] = Field(
        default_factory=dict,
        description=(
            'Per-logger levels from BAND_LOG_OVERRIDES JSON, e.g. {"httpx": "WARNING"}.'
        ),
    )

    @field_validator("log_level", "log_root_level", mode="before")
    @classmethod
    def _coerce_level(cls, value: object) -> str:
        return coerce_log_level_name(value, name="log level")

    @field_validator("log_file_level", mode="before")
    @classmethod
    def _coerce_optional_level(cls, value: object) -> str | None:
        if value is None:
            return None
        return coerce_log_level_name(value, name="log level")

    @field_validator("log_overrides")
    @classmethod
    def _known_override_levels(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, level in value.items():
            if not name:
                raise ValueError("log_overrides keys must be non-empty logger names")
            normalized[name] = coerce_log_level_name(
                level, name=f"log_overrides[{name!r}]"
            )
        return normalized

    def build_config(
        self,
        *,
        extra_loggers: Mapping[str, LogLevel] | None = None,
        json_fields: Sequence[str] | None = None,
        static_fields: Mapping[str, Any] | None = None,
        fmt: str | None = None,
        fmt_style: FormatStyle = FormatStyle.PERCENT,
        datefmt: str = "%Y-%m-%d %H:%M:%S",
    ) -> LoggingConfig:
        """Build a ``dictConfig`` dictionary without applying it."""
        return build_logging_config(
            self.log_level,
            style=self.log_console_style,
            root_level=self.log_root_level,
            stream=self.log_stream,
            datefmt=datefmt,
            fmt=fmt,
            fmt_style=fmt_style,
            extra_loggers=self._merged_loggers(extra_loggers),
            json_fields=json_fields,
            static_fields=static_fields,
            log_file=self.log_file,
            max_bytes=self.log_max_bytes,
            backup_count=self.log_backups,
            file_style=self.log_file_style if self.log_file is not None else None,
            file_level=self.log_file_level,
        )

    def configure(
        self,
        *,
        extra_loggers: Mapping[str, LogLevel] | None = None,
        json_fields: Sequence[str] | None = None,
        static_fields: Mapping[str, Any] | None = None,
        fmt: str | None = None,
        fmt_style: FormatStyle = FormatStyle.PERCENT,
        datefmt: str = "%Y-%m-%d %H:%M:%S",
    ) -> LoggingConfig:
        """Build and apply the logging configuration."""
        return configure_logging(
            self.log_level,
            style=self.log_console_style,
            root_level=self.log_root_level,
            stream=self.log_stream,
            datefmt=datefmt,
            fmt=fmt,
            fmt_style=fmt_style,
            extra_loggers=self._merged_loggers(extra_loggers),
            json_fields=json_fields,
            static_fields=static_fields,
            log_file=self.log_file,
            max_bytes=self.log_max_bytes,
            backup_count=self.log_backups,
            file_style=self.log_file_style if self.log_file is not None else None,
            file_level=self.log_file_level,
        )

    @classmethod
    def create(cls, **fields: Any) -> Self:
        """Build settings from optional fields.

        ``None`` values are omitted so environment variables and defaults still
        apply. Useful for CLI flags: ``LogSettings.create(log_level=args.log_level)``
        leaves ``BAND_LOG_LEVEL`` alone when the flag was not passed.
        """
        return cls(
            **{name: value for name, value in fields.items() if value is not None}
        )

    def for_application(self) -> Self:
        """Show this process's own logs at the same level as Band.

        By default only the ``band`` logger is raised to ``BAND_LOG_LEVEL``;
        everything else stays at root ``WARNING``. Call this in scripts, runners,
        and CLIs that log under ``__main__`` or other non-``band`` names — otherwise
        their ``INFO`` lines are silent.

        Leave it out when Band is embedded in a larger app and you only want Band
        diagnostics. An explicit ``BAND_LOG_ROOT_LEVEL`` / ``log_root_level`` is
        never overwritten.
        """
        if "log_root_level" in self.model_fields_set:
            return self
        return self.model_copy(update={"log_root_level": self.log_level})

    def _merged_loggers(
        self,
        extra_loggers: Mapping[str, LogLevel] | None,
    ) -> dict[str, LogLevel] | None:
        # Consumer defaults first; BAND_LOG_OVERRIDES wins on conflict.
        if not extra_loggers and not self.log_overrides:
            return None
        merged: dict[str, LogLevel] = dict(extra_loggers or {})
        merged.update(self.log_overrides)
        return merged


def configure_logging_from_env(
    *,
    extra_loggers: Mapping[str, LogLevel] | None = None,
    json_fields: Sequence[str] | None = None,
    static_fields: Mapping[str, Any] | None = None,
    fmt: str | None = None,
    fmt_style: FormatStyle = FormatStyle.PERCENT,
    datefmt: str = "%Y-%m-%d %H:%M:%S",
) -> LoggingConfig:
    """Load ``LogSettings`` from the environment and apply it."""
    return LogSettings().configure(
        extra_loggers=extra_loggers,
        json_fields=json_fields,
        static_fields=static_fields,
        fmt=fmt,
        fmt_style=fmt_style,
        datefmt=datefmt,
    )
