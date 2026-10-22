from __future__ import annotations

import sys
import unittest
from typing import ClassVar, TypedDict


class EnvironmentOptions(TypedDict, total=False):
    env: dict[str, str] | None


class CommandEnvironmentFixture(unittest.TestCase):
    parent: ClassVar[dict[str, str]] = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": "Pacific/Honolulu",
        "OPENAI_API_KEY": "synthetic-key-canary",
        "ROOM_TOKEN": "synthetic-room-canary",
        "HOME": "/synthetic-host-home",
        "PYTHONPATH": "/synthetic-import-canary",
    }

    probe: ClassVar[list[str]] = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        (
            "import json,os; print(json.dumps({'keys': sorted(os.environ), "
            "'path': os.environ.get('PATH'), 'tz': os.environ.get('TZ')}))"
        ),
    ]
