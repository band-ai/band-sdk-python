"""Shared cross-field validation rules used by tool input models and their
``AgentTools``-level backstops (some adapters hand-register tools as plain
functions and never construct/validate the input model)."""

from __future__ import annotations


def _join_with_or(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} or {names[1]}"
    return ", ".join(names[:-1]) + f", or {names[-1]}"


def at_least_one_of(**fields: object) -> None:
    """Raise ``ValueError`` unless at least one keyword argument is not ``None``."""
    if not fields:
        raise TypeError("at_least_one_of() requires at least one keyword argument")
    if all(value is None for value in fields.values()):
        raise ValueError(f"At least one of {_join_with_or(list(fields))} must be set")
