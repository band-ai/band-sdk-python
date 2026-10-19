"""Trusted, stdlib-only entry for the Windows Codex job helper."""

from __future__ import annotations

from pathlib import Path
from runpy import run_path

if __name__ == "__main__":
    run_path(str(Path(__file__).with_name("winspawn.py")), run_name="__main__")
