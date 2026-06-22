"""Logging configuration helpers for Band SDK applications."""

from __future__ import annotations

import importlib.util
import logging
import logging.config
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from band.core.exceptions import BandConfigError

LoggingStyle = Literal["standard", "rich", "json"]
LogLevel = int | str
LogStream = Literal["stderr", "stdout"]

_STANDARD_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_JSON_DEFAULT_FIELDS = ("asctime", "levelname", "name", "message")
_JSON_RENAME_FIELDS = {
    "asctime": "timestamp",
    "levelname": "level",
    "name": "logger",
}


def build_logging_config(
    level: LogLevel = logging.INFO,
    *,
    style: LoggingStyle = "standard",
    root_level: LogLevel = logging.WARNING,
    stream: LogStream = "stderr",
    datefmt: str = "%Y-%m-%d %H:%M:%S",
    extra_loggers: Mapping[str, LogLevel] | None = None,
    json_fields: Sequence[str] | None = None,
    static_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a normalized ``logging.config.dictConfig`` dictionary.

    The default keeps noisy dependencies at WARNING while enabling Band SDK
    logs at INFO. Applications can inspect, modify, then apply the returned
    dict themselves, or call :func:`configure_logging`.
    """
    normalized_style = _normalize_style(style)
    normalized_stream = _normalize_stream(stream)
    band_level = _normalize_level(level, name="level")
    normalized_root_level = _normalize_level(root_level, name="root_level")

    formatter_name = "console"
    handler: dict[str, Any] = {
        "class": "logging.StreamHandler",
        "formatter": formatter_name,
        "stream": f"ext://sys.{normalized_stream}",
    }

    formatters: dict[str, dict[str, Any]]
    if normalized_style == "standard":
        formatters = {
            formatter_name: {
                "format": _STANDARD_FORMAT,
                "datefmt": datefmt,
            }
        }
    elif normalized_style == "rich":
        _require_optional_package("rich", style="rich", extra="logging")
        handler = {
            "class": "rich.logging.RichHandler",
            "formatter": formatter_name,
            "rich_tracebacks": True,
            "markup": False,
            "show_path": False,
        }
        formatters = {
            formatter_name: {
                "format": "%(message)s",
                "datefmt": datefmt,
            }
        }
    else:
        _require_optional_package(
            "pythonjsonlogger",
            style="json",
            extra="logging",
            package_name="python-json-logger",
        )
        fields = tuple(json_fields or _JSON_DEFAULT_FIELDS)
        _validate_json_fields(fields)
        json_formatter: dict[str, Any] = {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": " ".join(f"%({field})s" for field in fields),
            "datefmt": datefmt,
            "rename_fields": {
                field: renamed
                for field, renamed in _JSON_RENAME_FIELDS.items()
                if field in fields
            },
        }
        if static_fields:
            json_formatter["static_fields"] = dict(static_fields)
        formatters = {formatter_name: json_formatter}

    loggers: dict[str, dict[str, Any]] = {
        "band": {
            "level": band_level,
            "propagate": True,
        }
    }
    if extra_loggers:
        for logger_name, logger_level in extra_loggers.items():
            if not logger_name:
                raise ValueError("extra_loggers keys must be non-empty logger names")
            loggers[logger_name] = {
                "level": _normalize_level(
                    logger_level,
                    name=f"extra_loggers[{logger_name!r}]",
                ),
                "propagate": True,
            }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": {
            "console": handler,
        },
        "root": {
            "level": normalized_root_level,
            "handlers": ["console"],
        },
        "loggers": loggers,
    }


def configure_logging(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Build and apply Band's logging configuration.

    Returns the applied ``dictConfig`` dictionary so callers can inspect the
    exact configuration.
    """
    config = build_logging_config(*args, **kwargs)
    logging.config.dictConfig(config)
    return config


def _normalize_style(style: str) -> LoggingStyle:
    if not isinstance(style, str):
        raise ValueError("style must be one of: standard, rich, json")
    normalized = style.lower()
    if normalized not in {"standard", "rich", "json"}:
        raise ValueError("style must be one of: standard, rich, json")
    return normalized  # type: ignore[return-value]


def _normalize_stream(stream: str) -> LogStream:
    if not isinstance(stream, str):
        raise ValueError("stream must be one of: stderr, stdout")
    normalized = stream.lower()
    if normalized not in {"stderr", "stdout"}:
        raise ValueError("stream must be one of: stderr, stdout")
    return normalized  # type: ignore[return-value]


def _normalize_level(level: LogLevel, *, name: str) -> LogLevel:
    if isinstance(level, int):
        if level < 0:
            raise ValueError(f"{name} must be a non-negative logging level")
        return level
    if isinstance(level, str):
        normalized = level.upper()
        if normalized in logging.getLevelNamesMapping():
            return normalized
        raise ValueError(f"{name} must be a valid logging level")
    raise ValueError(f"{name} must be an int or logging level name")


def _validate_json_fields(fields: Sequence[str]) -> None:
    if not fields:
        raise ValueError("json_fields must contain at least one field")
    for field in fields:
        if not field or not isinstance(field, str):
            raise ValueError("json_fields must contain non-empty strings")


def _require_optional_package(
    import_name: str,
    *,
    style: str,
    extra: str,
    package_name: str | None = None,
) -> None:
    if importlib.util.find_spec(import_name) is not None:
        return
    dependency = package_name or import_name
    raise BandConfigError(
        f"Logging style {style!r} requires optional dependency {dependency!r}. "
        f"Install it with: pip install 'band-sdk[{extra}]'"
    )
