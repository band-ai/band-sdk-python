"""ACP permission options and elicitation request fields."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

PermissionHandler = Callable[..., Awaitable[dict[str, object]]]
PermissionNarrator = Callable[[Awaitable[None]], Awaitable[None]]
ElicitationHandler = Callable[..., Awaitable[object]]
ElicitationNarrator = Callable[[Awaitable[None]], Awaitable[None]]

# ACP requires selecting an offered option id, never a synthesized grant.
ALLOW_ALWAYS_KIND = "allow_always"
_ALLOW_OPTION_KINDS = ("allow_once", ALLOW_ALWAYS_KIND)


def _resolve_option_id(option: object) -> str | None:
    """One offered option's wire id, preferring ``optionId`` over ``option_id``."""
    if isinstance(option, Mapping):
        option_id = option.get("optionId")
        if option_id is None:
            option_id = option.get("option_id")
    else:
        option_id = getattr(option, "option_id", None)
        if option_id is None:
            option_id = getattr(option, "optionId", None)
    return str(option_id) if option_id is not None else None


def permission_option_ids(options: object) -> tuple[str, ...]:
    """The wire option ids offered by a permission/tool-call request, in order."""
    if not isinstance(options, (list, tuple)):
        return ()
    return tuple(
        option_id
        for option in options
        if (option_id := _resolve_option_id(option)) is not None
    )


def option_id_of_kind(options: object, kind: str) -> str | None:
    """The ``optionId`` of the first offered option of ``kind``, else None."""
    if not isinstance(options, (list, tuple)):
        return None
    for option in options:
        option_kind = (
            option.get("kind")
            if isinstance(option, Mapping)
            else getattr(option, "kind", None)
        )
        option_id = _resolve_option_id(option)
        if option_kind == kind and option_id is not None:
            return option_id
    return None


def select_allow_option_id(options: object) -> str | None:
    """The ``optionId`` of an allow option offered in a permission request, else None."""
    for kind in _ALLOW_OPTION_KINDS:
        if (option_id := option_id_of_kind(options, kind)) is not None:
            return option_id
    return None


def allow_permission(option_id: str) -> dict[str, object]:
    """An ACP ``RequestPermissionResponse`` selecting (granting) ``option_id``."""
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def cancel_permission() -> dict[str, object]:
    """An ACP ``RequestPermissionResponse`` cancelling the request."""
    return {"outcome": {"outcome": "cancelled"}}


def elicitation_session_id(mode: object, kwargs: dict[str, object]) -> str:
    """Session id from ACP ``mode`` or test kwargs (camelCase / snake_case)."""
    return str(
        getattr(mode, "session_id", None)
        or kwargs.get("session_id")
        or kwargs.get("sessionId")
        or ""
    )


def elicitation_requested_schema(
    mode: object, kwargs: dict[str, object]
) -> object | None:
    """Form schema from ACP ``mode`` or test kwargs (camelCase / snake_case)."""
    return (
        getattr(mode, "requested_schema", None)
        or kwargs.get("requested_schema")
        or kwargs.get("requestedSchema")
    )
