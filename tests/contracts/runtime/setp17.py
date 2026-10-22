from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

CRASH = r"""
import os, sys
from remote_agent.store import Store
from remote_agent.hostkey import HostKey
store = Store(sys.argv[1])
original = os.fdopen
class PartialWriter:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def __getattr__(self, name): return getattr(self.stream, name)
    def write(self, data):
        self.stream.write(data[:7])
        self.stream.flush()
        os.fsync(self.stream.fileno())
        os._exit(91)
def interrupted(descriptor, *args, **kwargs):
    return PartialWriter(original(descriptor, *args, **kwargs))
os.fdopen = interrupted
HostKey(store).ensure()
"""


class AtomicInitialKeyFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.sqlite3"


class CompetingKeyPublication:
    def __init__(self, path: Path, sync: Callable[[int], None] = os.fsync) -> None:
        self.path = path
        self.sync = sync
        self.published = False

    def __call__(self, descriptor: int) -> None:
        self.sync(descriptor)
        if not self.published:
            self.published = True
            self.path.write_bytes(b"r" * 32)
            self.path.chmod(0o600)
