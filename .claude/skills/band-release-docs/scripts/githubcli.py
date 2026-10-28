"""Run GitHub CLI commands without exposing private command output."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class GhModel(BaseModel):
    """A ``gh --json`` object; field names are the camelCase ``gh`` keys."""

    model_config = ConfigDict(
        frozen=True, alias_generator=to_camel, validate_by_name=True, extra="ignore"
    )


class CommandError(RuntimeError):
    """A failed ``git`` or ``gh`` call.

    Its message names only the subcommand and exit code: the output of a call
    on the private Fern repository may name its files. ``detail`` holds stderr.
    """

    def __init__(self, command: str, code: int, detail: str) -> None:
        super().__init__(f"{command} failed (exit {code})")
        self.detail = detail


def gh(
    *args: str, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args], check=False, capture_output=True, text=True, env=env
    )


def gh_failed(
    args: Sequence[str], result: subprocess.CompletedProcess[str]
) -> Exception:
    return CommandError(
        f"gh {' '.join(args[:2])}", result.returncode, result.stderr.strip()
    )


def gh_output(*args: str, env: Mapping[str, str] | None = None) -> str:
    """Run ``gh`` and return its stdout; raise on a non-zero exit."""
    result = gh(*args, env=env)
    if result.returncode != 0:
        raise gh_failed(args, result)
    return result.stdout
