"""The failure handling and logging every Parlant tool call goes through.

Parlant shows the model only a generic "Tool call error" for an exception a
tool raises, so every failure the model should act on is returned as a
``ToolResult`` instead.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

from parlant.core.tools import ToolResult

from band.core.exceptions import BandToolError
from band.integrations.parlant.mentions import with_mention_handles
from band.integrations.parlant.sessiontools import (
    CONTEXT_PARAMETER,
    NO_SESSION_TOOLS_ERROR,
    NoSessionTools,
    get_session_tools,
)

logger = logging.getLogger(__name__)

# Every log line this module emits is tagged with it, so a Parlant run's tool
# activity greps out of a mixed log in one pass.
LOG_PREFIX = "[Parlant Tool]"

# Longest argument value echoed into the per-call log line; a full message body
# or file payload would otherwise dominate the log.
LOGGED_VALUE_CHARS = 50


def _logged_arguments(call: inspect.BoundArguments) -> str:
    """The call's own arguments, truncated, for one per-tool log line."""
    rendered = ", ".join(
        f"{name}={str(value)[:LOGGED_VALUE_CHARS]}"
        for name, value in call.arguments.items()
        if name != CONTEXT_PARAMETER
    )
    return f", {rendered}" if rendered else ""


def _failure_message(exc: Exception, *, context: Any, mention_hints: bool) -> str:
    """``exc`` as the model reads it, with the room's handles for a bad mention."""
    message = str(exc)
    if mention_hints and isinstance(exc, (ValueError, BandToolError)):
        session_tools = get_session_tools(context.session_id)
        if session_tools:
            message = with_mention_handles(message, session_tools)
    return message


def guard_failures(
    func: Callable[..., Any], *, failure: str, mention_hints: bool
) -> Callable[..., Any]:
    """Wrap *func* with the logging and failure handling every tool shares.

    ``failure`` completes ``"Error {failure}: {exc}"`` and may template the
    call's own arguments (e.g. ``"adding participant '{identifier}'"``);
    ``mention_hints`` appends the room's available handles to a
    mention-related failure. ``functools.wraps`` is load-bearing: Parlant
    introspects ``__wrapped__``, so the registered signature is *func*'s own.
    """

    signature = inspect.signature(func)
    defaulted = {
        name
        for name, param in signature.parameters.items()
        if param.default is not inspect.Parameter.empty
    }

    @functools.wraps(func)
    async def run(context: Any, *args: Any, **kwargs: Any) -> Any:
        # Parlant's engine sends None for every optional the model omitted;
        # dropping it lets the parameter's own default apply. A required
        # parameter has no default to fall back on, so its None is kept.
        kwargs = {
            name: value
            for name, value in kwargs.items()
            if value is not None or name not in defaulted
        }
        # bind() gets its own try: a signature/argument-shape mismatch raises
        # TypeError before there is any `call.arguments` to build the usual
        # failure message from.
        try:
            call = signature.bind(context, *args, **kwargs)
            call.apply_defaults()
        except TypeError as exc:
            logger.exception(
                "%s %s: malformed call arguments", LOG_PREFIX, func.__name__
            )
            return ToolResult(data=f"Error calling {func.__name__}: {exc}")

        logger.info(
            "%s %s called: session=%s%s",
            LOG_PREFIX,
            func.__name__,
            context.session_id,
            _logged_arguments(call),
        )
        try:
            result = await func(context, *args, **kwargs)
        except NoSessionTools:
            logger.error(
                "%s %s: no tools available for session %s",
                LOG_PREFIX,
                func.__name__,
                context.session_id,
            )
            return ToolResult(data=NO_SESSION_TOOLS_ERROR)
        except Exception as exc:
            context_phrase = failure.format(**call.arguments)
            logger.exception("%s Error %s", LOG_PREFIX, context_phrase)
            message = _failure_message(
                exc, context=context, mention_hints=mention_hints
            )
            return ToolResult(data=f"Error {context_phrase}: {message}")
        logger.info("%s %s -> %s", LOG_PREFIX, func.__name__, result)
        return result

    return run
