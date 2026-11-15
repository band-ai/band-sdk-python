"""Agent workspace roots that keep their parent directory's permissions.

``tempfile.mkdtemp`` and pytest's ``tmp_path`` create directories with mode
``0o700``, which Python 3.12.4+ applies on Windows as a protected owner-only
ACL. Codex's unelevated Windows sandbox cannot work in a directory under one,
so a native agent's workspace root inherits the temp directory's permissions.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from types import TracebackType


class InheritedTemporaryDirectory:
    """A temporary directory created with its parent's inherited permissions."""

    def __init__(self, prefix: str, parent: str | None = None) -> None:
        root = Path(parent or tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex}"
        root.mkdir()
        self.name = str(root)

    def cleanup(self) -> None:
        shutil.rmtree(self.name)

    def __enter__(self) -> str:
        return self.name

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.cleanup()
