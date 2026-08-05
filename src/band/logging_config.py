"""Logging configuration helpers for Band SDK applications."""

from __future__ import annotations

import importlib.util
import logging
import logging.config
import logging.handlers
import os
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import IO, Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from band.core.exceptions import BandConfigError


class LoggingStyle(StrEnum):
    """Console (and shared) formatter styles for :func:`configure_logging`.

    Single source of truth for the style vocabulary. ``FileStyle`` is the
    non-rich subset. Members compare equal to their string values, so
    ``style="json"`` and ``LoggingStyle.JSON`` are interchangeable.
    """

    STANDARD = "standard"
    RICH = "rich"
    JSON = "json"


# File sinks reject rich (TTY-oriented); derived so STANDARD/JSON stay shared.
FileStyle = Literal[LoggingStyle.STANDARD, LoggingStyle.JSON]


class FormatStyle(StrEnum):
    """``logging.Formatter`` format-string styles."""

    PERCENT = "%"
    BRACE = "{"
    DOLLAR = "$"


class LogStream(StrEnum):
    """Console stream target."""

    STDERR = "stderr"
    STDOUT = "stdout"


LogLevel: TypeAlias = int | str
LoggingConfig: TypeAlias = dict[str, Any]

# Band's HTTP/WS stack narrates every request or frame at INFO. Not applied by
# configure_logging — pass via extra_loggers / LogSettings when a consumer wants
# them quiet (WARNING) or loud (DEBUG). Framework-specific names stay local.
CHATTY_LOGGERS: tuple[str, ...] = (
    "httpx",
    "httpcore",
    "phoenix_channels_python_client",
)

# Default standard-style format. Override via fmt= only when necessary
# (for example message-only provisioning scripts).
STANDARD_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# The JSON formatter lives in pythonjsonlogger.json, which 3.1.0 split out of
# pythonjsonlogger.jsonlogger. Named once so the install hint and the dependency
# floor in pyproject.toml cannot drift apart.
JSON_LOGGER_REQUIREMENT = "python-json-logger>=3.1.0"

# Record attributes OpenTelemetry's LoggingInstrumentor writes when started with
# inject_trace_context=True. Part of the *default* JSON schema: absent they
# serialize as null, present they carry the live trace, so a log pipeline reads
# one shape whether or not the host instrumented the process. A caller who
# chooses json_fields replaces that default and splices these back in to keep
# correlation.
OTEL_CORRELATION_FIELDS: tuple[str, ...] = (
    "otelTraceID",
    "otelSpanID",
    "otelTraceSampled",
    "otelServiceName",
)
_JSON_DEFAULT_FIELDS = (
    "asctime",
    "levelname",
    "name",
    "message",
    *OTEL_CORRELATION_FIELDS,
)
_JSON_RENAME_FIELDS = {
    "asctime": "timestamp",
    "levelname": "level",
    "name": "logger",
}


# Band logs message content at DEBUG in several places (prompt text, tool
# payloads), so a log file can hold room content. Band hardens only what it
# creates: an existing directory or file belongs to the operator, and narrowing
# it reaches outside Band entirely — as root, a log path under /tmp would take
# the whole box's 1777 down to 0700.
LOG_FILE_MODE = 0o600
LOG_DIR_MODE = 0o700


class OwnerOnlyCreate:
    """Give log files an owner-only mode at the moment they are created.

    A ``chmod`` after configure only ever protects the first file:
    ``RotatingFileHandler`` opens a fresh one on every rollover, under the
    process umask, so a long-lived agent's live log drifts back to ``0644``
    exactly when it holds the most room content. Setting the mode inside
    ``_open`` covers the first file and every rollover with one rule, and the
    backups inherit it because a rollover renames the live file.

    A file that already exists is opened untouched — its mode is the operator's
    call, the same rule :func:`create_log_directory` follows.
    """

    baseFilename: str  # noqa: N815 - the attribute logging.FileHandler defines

    def _open(self) -> IO[Any]:
        try:
            descriptor = os.open(
                self.baseFilename,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                LOG_FILE_MODE,
            )
        except FileExistsError:
            pass  # Not ours to re-mode; the handler opens it as it finds it.
        else:
            # The creation mode is masked by umask and fchmod is not, so the
            # pair lands on 0600 whatever the host process set. Windows has no
            # fchmod before 3.13 and honors only the read-only bit anyway, so
            # there the creation mode is the whole (best-effort) story.
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, LOG_FILE_MODE)
            os.close(descriptor)
        return super()._open()  # type: ignore[misc]


class OwnerOnlyFileHandler(OwnerOnlyCreate, logging.FileHandler):
    """``FileHandler`` whose file is created owner-only."""


class OwnerOnlyRotatingFileHandler(
    OwnerOnlyCreate, logging.handlers.RotatingFileHandler
):
    """``RotatingFileHandler`` whose every generation is created owner-only."""


def create_log_directory(path: Path) -> None:
    """Create the log file's missing parent directories, owner-only.

    Only the segments Band actually creates are hardened. Anything already
    there is left exactly as the operator has it — Band's job is to not widen
    a directory's permissions, not to narrow them.

    Each segment is created individually rather than with ``parents=True``,
    which applies its ``mode`` to the leaf alone and leaves every intermediate
    directory at the umask default. Losing the race to another process is not
    an error: that directory is then simply one Band did not create.
    """
    missing: list[Path] = []
    directory = path.expanduser().resolve().parent
    while not directory.exists() and directory.parent != directory:
        missing.append(directory)
        directory = directory.parent
    for segment in reversed(missing):
        try:
            segment.mkdir(mode=LOG_DIR_MODE)
        except FileExistsError:
            continue


def coerce_log_level(value: object, *, name: str = "level") -> LogLevel:
    """Normalize a logging level for pydantic validators and call sites."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an int or logging level name")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{name} must be a non-negative logging level")
        return value
    if isinstance(value, str):
        normalized = value.upper()
        if normalized in logging.getLevelNamesMapping():
            return normalized
        raise ValueError(f"{name} must be a valid logging level")
    raise ValueError(f"{name} must be an int or logging level name")


def coerce_log_level_name(value: object, *, name: str = "level") -> str:
    """Like :func:`coerce_log_level`, but always returns an uppercase level name."""
    level = coerce_log_level(value, name=name)
    if isinstance(level, str):
        return level
    level_name = logging.getLevelName(level)
    if not isinstance(level_name, str) or level_name.startswith("Level "):
        raise ValueError(f"{name} must be a valid logging level")
    return level_name


def level_value(level: LogLevel) -> int:
    """Numeric severity for comparing two levels."""
    if isinstance(level, int):
        return level
    return logging.getLevelNamesMapping()[str(level).upper()]


def more_verbose(left: LogLevel, right: LogLevel) -> LogLevel:
    """The more verbose (lower numeric) of two levels."""
    return left if level_value(left) <= level_value(right) else right


def chatty_logger_levels(level: LogLevel = "WARNING") -> dict[str, LogLevel]:
    """Map :data:`CHATTY_LOGGERS` to ``level`` for ``extra_loggers``."""
    return dict.fromkeys(CHATTY_LOGGERS, level)


def _lowercase_literal(value: object) -> object:
    return value.lower() if isinstance(value, str) else value


def _reject_bool_int(value: object, info: ValidationInfo) -> object:
    if isinstance(value, bool):
        raise ValueError(f"{info.field_name} must be a non-negative int")
    return value


def _coerce_named_level(value: object, info: ValidationInfo) -> LogLevel:
    return coerce_log_level(value, name=info.field_name or "level")


def _coerce_optional_level(value: object, info: ValidationInfo) -> LogLevel | None:
    if value is None:
        return None
    return coerce_log_level(value, name=info.field_name or "file_level")


def _coerce_log_file(value: object) -> Path | None:
    if value is None:
        return None
    return Path(value)  # type: ignore[arg-type]


def _coerce_extra_loggers(
    value: object,
) -> dict[str, LogLevel] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("extra_loggers must be a mapping of logger name to level")
    normalized: dict[str, LogLevel] = {}
    for logger_name, logger_level in value.items():
        if not logger_name or not isinstance(logger_name, str):
            raise ValueError("extra_loggers keys must be non-empty logger names")
        normalized[logger_name] = coerce_log_level(
            logger_level,
            name=f"extra_loggers[{logger_name!r}]",
        )
    return normalized


def _coerce_json_fields(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("json_fields must contain non-empty strings")
    fields = tuple(value)
    if not fields:
        raise ValueError("json_fields must contain at least one field")
    for field in fields:
        if not field or not isinstance(field, str):
            raise ValueError("json_fields must contain non-empty strings")
    return fields


class LoggingRequest(BaseModel):
    """Validated inputs for :func:`build_logging_config` / :func:`configure_logging`."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    level: Annotated[LogLevel, BeforeValidator(_coerce_named_level)] = logging.INFO
    style: Annotated[LoggingStyle, BeforeValidator(_lowercase_literal)] = (
        LoggingStyle.STANDARD
    )
    root_level: Annotated[LogLevel, BeforeValidator(_coerce_named_level)] = (
        logging.WARNING
    )
    stream: Annotated[LogStream, BeforeValidator(_lowercase_literal)] = LogStream.STDERR
    datefmt: str = "%Y-%m-%d %H:%M:%S"
    fmt: str | None = None
    fmt_style: FormatStyle = FormatStyle.PERCENT
    extra_loggers: Annotated[
        dict[str, LogLevel] | None, BeforeValidator(_coerce_extra_loggers)
    ] = None
    json_fields: Annotated[
        tuple[str, ...] | None, BeforeValidator(_coerce_json_fields)
    ] = None
    static_fields: dict[str, Any] | None = None
    log_file: Annotated[Path | None, BeforeValidator(_coerce_log_file)] = None
    max_bytes: Annotated[int, BeforeValidator(_reject_bool_int)] = Field(
        default=0, ge=0
    )
    backup_count: Annotated[int, BeforeValidator(_reject_bool_int)] = Field(
        default=1, ge=0
    )
    file_style: Annotated[FileStyle | None, BeforeValidator(_lowercase_literal)] = None
    file_level: Annotated[LogLevel | None, BeforeValidator(_coerce_optional_level)] = (
        None
    )

    @model_validator(mode="after")
    def _rotation_needs_a_backup(self) -> LoggingRequest:
        # RotatingFileHandler with backupCount=0 reopens the same file in append
        # mode instead of rotating, so a size cap with no backups grows forever —
        # exactly what the caller was trying to prevent.
        if self.max_bytes > 0 and self.backup_count == 0:
            raise ValueError(
                "backup_count must be at least 1 when max_bytes is set; "
                "a rotating handler with no backups never rotates"
            )
        return self

    @property
    def resolved_file_style(self) -> FileStyle | None:
        if self.log_file is None:
            return None
        if self.file_style is not None:
            return self.file_style
        return LoggingStyle.STANDARD

    @property
    def resolved_file_level(self) -> LogLevel | None:
        if self.log_file is None:
            return None
        return self.file_level if self.file_level is not None else self.level

    @property
    def effective_band_level(self) -> LogLevel:
        file_level = self.resolved_file_level
        if file_level is None:
            return self.level
        return more_verbose(self.level, file_level)


def build_logging_config(
    level: LogLevel = logging.INFO,
    *,
    style: LoggingStyle = LoggingStyle.STANDARD,
    root_level: LogLevel = logging.WARNING,
    stream: LogStream = LogStream.STDERR,
    datefmt: str = "%Y-%m-%d %H:%M:%S",
    fmt: str | None = None,
    fmt_style: FormatStyle = FormatStyle.PERCENT,
    extra_loggers: Mapping[str, LogLevel] | None = None,
    json_fields: Sequence[str] | None = None,
    static_fields: Mapping[str, Any] | None = None,
    log_file: Path | str | None = None,
    max_bytes: int = 0,
    backup_count: int = 1,
    file_style: FileStyle | None = None,
    file_level: LogLevel | None = None,
) -> LoggingConfig:
    """Build a normalized ``logging.config.dictConfig`` dictionary.

    The default keeps noisy dependencies at WARNING while enabling Band SDK
    logs at INFO. Applications can inspect, modify, then apply the returned
    dict themselves, or call :func:`configure_logging`.

    This function touches nothing: applying the result yourself with
    ``log_file`` set means creating the file's parent directory first, which
    :func:`configure_logging` does for you. Applying it yourself with
    ``dictConfig`` also means ``band`` and any ``extra_loggers`` name gets its
    existing handlers cleared — ``dictConfig``'s ``loggers`` section is always
    non-incremental, and this function must list those names to express their
    level. Use :func:`configure_logging` if a named logger's own handlers need
    to survive.

    Args:
        level: Logging level for the ``band`` logger (and the console handler
            when a quieter/louder file sink is also configured). Accepts
            logging constants like ``logging.INFO`` or names like ``"INFO"``.
        style: Console output style: ``"standard"``, ``"rich"``, or ``"json"``.
            ``"rich"`` and ``"json"`` require ``band-sdk[logging]``.
        root_level: Root logger level for non-Band loggers.
        stream: Console stream: ``"stderr"`` or ``"stdout"``.
        datefmt: Timestamp format used by standard, Rich, and JSON output.
        fmt: Optional custom format string for ``standard`` console/file
            formatters. Ignored for ``rich`` and ``json`` styles.
        fmt_style: Format string style for ``fmt`` (``"%"``, ``"{"``, or
            ``"$"``). Matches :class:`logging.Formatter`.
        extra_loggers: Optional logger-name to level mapping, for example
            ``{"httpx": "WARNING"}``.
        json_fields: LogRecord field names to include in JSON output. Replaces
            the default set, which ends in :data:`OTEL_CORRELATION_FIELDS` —
            splice those back in to keep trace correlation.
        static_fields: Fixed fields added to every JSON record.
        log_file: Optional path for a second file handler. ``None`` disables
            file logging.
        max_bytes: Max file size before rotation. ``0`` uses a plain
            :class:`~logging.FileHandler`; a positive value uses
            :class:`~logging.handlers.RotatingFileHandler`.
        backup_count: Rotated backup files to keep when ``max_bytes > 0``.
            Must be at least 1 there: a rotating handler with no backups never
            rotates. Ignored when ``max_bytes`` is ``0``.
        file_style: File formatter style: ``"standard"`` or ``"json"``.
            Defaults to ``"standard"`` when ``log_file`` is set.
        file_level: Level for the file handler. Defaults to ``level``. When
            more verbose than ``level``, only the ``band`` logger is lowered so
            its records reach the file while the console handler stays at
            ``level`` — this is not a sink-wide DEBUG capture. Every other
            logger stays at ``root_level`` unless named in ``extra_loggers``,
            which keeps whatever level you give it there regardless of
            ``file_level``.

    Examples:
        ``build_logging_config()``
        ``build_logging_config(style=LoggingStyle.JSON, stream=LogStream.STDOUT)``
        ``build_logging_config(level="DEBUG", extra_loggers={"httpx": "WARNING"})``
        ``build_logging_config(log_file="/var/log/band.log", max_bytes=1_000_000)``
    """
    try:
        request = LoggingRequest(
            level=level,
            style=style,
            root_level=root_level,
            stream=stream,
            datefmt=datefmt,
            fmt=fmt,
            fmt_style=fmt_style,
            extra_loggers=dict(extra_loggers) if extra_loggers is not None else None,
            json_fields=tuple(json_fields) if json_fields is not None else None,
            static_fields=dict(static_fields) if static_fields is not None else None,
            log_file=log_file,
            max_bytes=max_bytes,
            backup_count=backup_count,
            file_style=file_style,
            file_level=file_level,
        )
    except ValidationError as exc:
        raise _value_error_from_validation(exc) from exc
    return _build_from_request(request)


def _value_error_from_validation(exc: ValidationError) -> ValueError:
    """Surface the first pydantic field error as a plain ``ValueError``."""
    for error in exc.errors():
        message = error.get("msg", "")
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        loc = ".".join(str(part) for part in error.get("loc", ()))
        if loc and loc not in message:
            return ValueError(f"{loc}: {message}" if message else loc)
        if message:
            return ValueError(message)
    return ValueError(str(exc))


def configure_logging(
    level: LogLevel = logging.INFO,
    *,
    style: LoggingStyle = LoggingStyle.STANDARD,
    root_level: LogLevel = logging.WARNING,
    stream: LogStream = LogStream.STDERR,
    datefmt: str = "%Y-%m-%d %H:%M:%S",
    fmt: str | None = None,
    fmt_style: FormatStyle = FormatStyle.PERCENT,
    extra_loggers: Mapping[str, LogLevel] | None = None,
    json_fields: Sequence[str] | None = None,
    static_fields: Mapping[str, Any] | None = None,
    log_file: Path | str | None = None,
    max_bytes: int = 0,
    backup_count: int = 1,
    file_style: FileStyle | None = None,
    file_level: LogLevel | None = None,
) -> LoggingConfig:
    """Build and apply Band's logging configuration.

    Returns the same declarative dictionary :func:`build_logging_config` would
    (callers can inspect it), but does not hand ``loggers`` to ``dictConfig``
    directly: ``dictConfig`` is non-incremental and would unconditionally clear
    any handlers already attached to ``band`` or an ``extra_loggers`` name (a
    host-owned shipper, an OTEL ``LoggingHandler``). Instead it applies the
    root/handlers/formatters section, then sets each named logger's level with
    :meth:`logging.Logger.setLevel`, which leaves existing handlers and
    propagation on those loggers untouched. A direct :func:`build_logging_config`
    consumer who calls ``dictConfig`` on the raw dict does not get this
    protection — use :func:`configure_logging` when a named logger's own
    handlers must survive.

    Common forms:
        ``configure_logging()``
        ``configure_logging(style=LoggingStyle.RICH)``
        ``configure_logging(style=LoggingStyle.JSON, stream=LogStream.STDOUT)``
        ``configure_logging(level="DEBUG", extra_loggers={"httpx": "WARNING"})``
        ``configure_logging(log_file="agent.log", max_bytes=1_000_000)``

    See :func:`build_logging_config` for all supported options.
    """
    config = build_logging_config(
        level,
        style=style,
        root_level=root_level,
        stream=stream,
        datefmt=datefmt,
        fmt=fmt,
        fmt_style=fmt_style,
        extra_loggers=extra_loggers,
        json_fields=json_fields,
        static_fields=static_fields,
        log_file=log_file,
        max_bytes=max_bytes,
        backup_count=backup_count,
        file_style=file_style,
        file_level=file_level,
    )
    if log_file is not None:
        create_log_directory(Path(log_file))
    # The handlers create the file itself owner-only (OwnerOnlyCreate), which a
    # chmod here could not do for the files a rollover replaces.
    logging.config.dictConfig({**config, "loggers": {}})
    for logger_name, logger_config in config["loggers"].items():
        logging.getLogger(logger_name).setLevel(logger_config["level"])
    return config


def _build_from_request(request: LoggingRequest) -> LoggingConfig:
    formatters: dict[str, LoggingConfig] = {}
    handlers: dict[str, LoggingConfig] = {}

    console_handler, console_formatters = _build_console_handler(
        style=request.style,
        stream=request.stream,
        datefmt=request.datefmt,
        fmt=request.fmt,
        fmt_style=request.fmt_style,
        json_fields=request.json_fields,
        static_fields=request.static_fields,
    )
    formatters.update(console_formatters)
    handlers["console"] = console_handler
    root_handlers = ["console"]

    if request.log_file is not None:
        file_style = request.resolved_file_style
        file_level = request.resolved_file_level
        assert file_style is not None and file_level is not None

        file_handler, file_formatters = _build_file_handler(
            path=request.log_file,
            style=file_style,
            datefmt=request.datefmt,
            fmt=request.fmt,
            fmt_style=request.fmt_style,
            max_bytes=request.max_bytes,
            backup_count=request.backup_count,
            json_fields=request.json_fields,
            static_fields=request.static_fields,
        )
        formatters.update(file_formatters)
        handlers["file"] = file_handler
        root_handlers.append("file")

        # Logger gates before handlers: when the file is more verbose than the
        # console, lower the band logger and pin each handler's own level so
        # console stays quiet while the file captures detail. When the two agree
        # neither handler is pinned — a per-logger level from extra_loggers must
        # reach both sinks, not just the console.
        if level_value(file_level) != level_value(request.level):
            handlers["console"]["level"] = request.level
            handlers["file"]["level"] = file_level

    # Keep existing application loggers alive; SDK helpers should not silently
    # disable logging configured by the host process.
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "root": {
            "level": request.root_level,
            "handlers": root_handlers,
        },
        "loggers": _build_logger_configs(
            level=request.effective_band_level,
            extra_loggers=request.extra_loggers,
        ),
    }


def _build_logger_configs(
    *,
    level: LogLevel,
    extra_loggers: Mapping[str, LogLevel] | None,
) -> dict[str, LoggingConfig]:
    # Band logs are opt-in at INFO by default; unrelated dependencies stay at
    # the root level unless callers explicitly list them in extra_loggers.
    #
    # No "propagate" key: dictConfig's DictConfigurator.configure_logger only
    # assigns logger.propagate when the key is present, so omitting it sets a
    # level without reversing a host's own propagate=False on that logger.
    loggers: dict[str, LoggingConfig] = {"band": {"level": level}}
    if not extra_loggers:
        return loggers
    for logger_name, logger_level in extra_loggers.items():
        loggers[logger_name] = {"level": logger_level}
    return loggers


def _build_console_handler(
    *,
    style: LoggingStyle,
    stream: LogStream,
    datefmt: str,
    fmt: str | None,
    fmt_style: FormatStyle,
    json_fields: Sequence[str] | None,
    static_fields: Mapping[str, Any] | None,
) -> tuple[LoggingConfig, dict[str, LoggingConfig]]:
    formatter_name = "console"
    match style:
        case LoggingStyle.STANDARD:
            return _build_standard_stream_config(
                formatter_name=formatter_name,
                stream=stream,
                datefmt=datefmt,
                fmt=fmt,
                fmt_style=fmt_style,
            )
        case LoggingStyle.RICH:
            return _build_rich_config(
                formatter_name=formatter_name,
                stream=stream,
                datefmt=datefmt,
            )
        case LoggingStyle.JSON:
            return _build_json_stream_config(
                formatter_name=formatter_name,
                stream=stream,
                datefmt=datefmt,
                json_fields=json_fields,
                static_fields=static_fields,
            )
        case _:
            raise AssertionError(f"Unexpected logging style: {style!r}")


def _build_file_handler(
    *,
    path: Path,
    style: FileStyle,
    datefmt: str,
    fmt: str | None,
    fmt_style: FormatStyle,
    max_bytes: int,
    backup_count: int,
    json_fields: Sequence[str] | None,
    static_fields: Mapping[str, Any] | None,
) -> tuple[LoggingConfig, dict[str, LoggingConfig]]:
    formatter_name = "file"
    handler = _file_handler_config(
        path=path, max_bytes=max_bytes, backup_count=backup_count
    )
    handler["formatter"] = formatter_name

    match style:
        case LoggingStyle.STANDARD:
            formatters = {
                formatter_name: _standard_formatter(
                    datefmt=datefmt,
                    fmt=fmt,
                    fmt_style=fmt_style,
                )
            }
            return handler, formatters
        case LoggingStyle.JSON:
            _, formatters = _build_json_formatter(
                formatter_name=formatter_name,
                datefmt=datefmt,
                json_fields=json_fields,
                static_fields=static_fields,
            )
            return handler, formatters
        case _:
            raise AssertionError(f"Unexpected file style: {style!r}")


def _file_handler_config(
    *,
    path: Path,
    max_bytes: int,
    backup_count: int,
) -> LoggingConfig:
    filename = str(path.expanduser())
    if max_bytes > 0:
        return {
            "class": "band.logging_config.OwnerOnlyRotatingFileHandler",
            "filename": filename,
            "maxBytes": max_bytes,
            "backupCount": backup_count,
        }
    return {
        "class": "band.logging_config.OwnerOnlyFileHandler",
        "filename": filename,
    }


def _build_standard_stream_config(
    *,
    formatter_name: str,
    stream: LogStream,
    datefmt: str,
    fmt: str | None,
    fmt_style: FormatStyle,
) -> tuple[LoggingConfig, dict[str, LoggingConfig]]:
    handler: LoggingConfig = {
        "class": "logging.StreamHandler",
        "formatter": formatter_name,
        "stream": f"ext://sys.{stream}",
    }
    formatters = {
        formatter_name: _standard_formatter(
            datefmt=datefmt,
            fmt=fmt,
            fmt_style=fmt_style,
        )
    }
    return handler, formatters


def _standard_formatter(
    *,
    datefmt: str,
    fmt: str | None,
    fmt_style: FormatStyle,
) -> LoggingConfig:
    formatter: LoggingConfig = {
        "format": fmt if fmt is not None else STANDARD_FORMAT,
        "datefmt": datefmt,
    }
    if fmt_style != FormatStyle.PERCENT:
        formatter["style"] = fmt_style
    return formatter


def _build_rich_config(
    *,
    formatter_name: str,
    stream: LogStream,
    datefmt: str,
) -> tuple[LoggingConfig, dict[str, LoggingConfig]]:
    _require_optional_package("rich", style=LoggingStyle.RICH, extra="logging")
    # Rich needs a factory because stdout/stderr and date formatting are passed
    # through its Console/RichHandler constructors, not a plain StreamHandler.
    handler: LoggingConfig = {
        "()": "band.logging_config._build_rich_handler",
        "formatter": formatter_name,
        "stream": stream,
        "datefmt": datefmt,
    }
    formatters = {
        formatter_name: {
            "format": "%(message)s",
            "datefmt": datefmt,
        }
    }
    return handler, formatters


def _build_json_stream_config(
    *,
    formatter_name: str,
    stream: LogStream,
    datefmt: str,
    json_fields: Sequence[str] | None,
    static_fields: Mapping[str, Any] | None,
) -> tuple[LoggingConfig, dict[str, LoggingConfig]]:
    handler: LoggingConfig = {
        "class": "logging.StreamHandler",
        "formatter": formatter_name,
        "stream": f"ext://sys.{stream}",
    }
    _, formatters = _build_json_formatter(
        formatter_name=formatter_name,
        datefmt=datefmt,
        json_fields=json_fields,
        static_fields=static_fields,
    )
    return handler, formatters


def _build_json_formatter(
    *,
    formatter_name: str,
    datefmt: str,
    json_fields: Sequence[str] | None,
    static_fields: Mapping[str, Any] | None,
) -> tuple[None, dict[str, LoggingConfig]]:
    # The formatter path uses the v3 submodule; check that exact import so older
    # python-json-logger installs fail with our actionable BandConfigError.
    _require_optional_package(
        "pythonjsonlogger.json",
        style=LoggingStyle.JSON,
        extra="logging",
        package_name=JSON_LOGGER_REQUIREMENT,
    )
    fields = tuple(json_fields or _JSON_DEFAULT_FIELDS)
    json_formatter: LoggingConfig = {
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
    return None, {formatter_name: json_formatter}


def _build_rich_handler(*, stream: LogStream, datefmt: str) -> logging.Handler:
    from rich.console import Console
    from rich.logging import RichHandler

    # Do not let Rich default to stderr when callers requested stdout.
    output = sys.stdout if stream == LogStream.STDOUT else sys.stderr
    return RichHandler(
        console=Console(file=output),
        rich_tracebacks=True,
        markup=False,
        show_path=False,
        log_time_format=datefmt,
    )


def _require_optional_package(
    import_name: str,
    *,
    style: LoggingStyle,
    extra: str,
    package_name: str | None = None,
) -> None:
    # find_spec imports the parent package for a dotted name, so an entirely
    # missing dependency raises ModuleNotFoundError instead of returning None.
    # Treat both "missing parent" and "missing submodule" as not installed.
    try:
        spec = importlib.util.find_spec(import_name)
    except ModuleNotFoundError:
        spec = None
    if spec is not None:
        return
    dependency = package_name or import_name
    raise BandConfigError(
        f"Logging style {style!r} requires optional dependency {dependency!r}. "
        f"Install it with: pip install 'band-sdk[{extra}]'"
    )
