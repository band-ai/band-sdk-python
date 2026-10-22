from __future__ import annotations

CRASH = r"""
import os, sys
from remote_agent.store import Store
from remote_agent.hostkey import HostKey
store = Store(sys.argv[1])
host = HostKey(store)
if sys.argv[2] == 'write':
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
    os.fdopen = lambda fd, *args, **kwargs: PartialWriter(original(fd, *args, **kwargs))
else:
    commit = store.commit
    def interrupted(kind, *args, **kwargs):
        result = commit(kind, *args, **kwargs)
        if kind == 'host_key_rotation_prepared': os._exit(91)
        return result
    store.commit = interrupted
host.rotate()
"""
