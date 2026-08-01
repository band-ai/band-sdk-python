"""Shared deprecation warnings for public SDK surfaces."""

from __future__ import annotations

import warnings


class BandDeprecationWarning(FutureWarning):
    """Emitted when a deprecated public SDK surface is used."""


def warn_deprecated(
    old: str | None = None,
    new: str | None = None,
    removal: str = "2.0",
    *,
    stacklevel: int = 2,
    message: str | None = None,
) -> None:
    """Warn that a public surface is deprecated.

    Spellings:

    - ``warn_deprecated("old", "new")`` — standard replacement text
    - ``warn_deprecated(message="…")`` — message-only (no dummy old/new)
    """
    if message is not None:
        text = message
    elif old is not None and new is not None:
        text = (
            f"{old} is deprecated; use {new} instead. "
            f"Scheduled for removal in {removal}."
        )
    else:
        raise TypeError("warn_deprecated requires (old, new) or message=")
    warnings.warn(text, BandDeprecationWarning, stacklevel=stacklevel)
