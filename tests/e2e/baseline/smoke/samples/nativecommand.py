"""Quote a native shell invocation for the host's default shell."""

from __future__ import annotations

import os
import shlex
from collections.abc import Sequence


def shell_command(argv: Sequence[str]) -> str:
    if os.name == "nt":
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in argv)
    return shlex.join(argv)
