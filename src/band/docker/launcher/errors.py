"""The launcher's single failure type."""

from __future__ import annotations


class LaunchError(ValueError):
    """A launch failure attributed to a named phase.

    The message is safe to log: phases never interpolate secret values,
    authorization headers, or complete environments.
    """

    def __init__(self, phase: str, message: str) -> None:
        self.phase = phase
        super().__init__(f"[{phase}] {message}")
