"""Read the actual helper prelude in an isolated stdlib-only subprocess."""

from __future__ import annotations

import json
import os
import sys
from runpy import run_path

if __name__ == "__main__":
    spawn = run_path(sys.argv[1])
    env = spawn["read_environment"]()
    remainder = os.read(0, len(sys.argv[2]))
    sys.stdout.buffer.write(
        json.dumps([env, remainder.decode("ascii")]).encode("ascii")
    )
