"""Shared skip markers for platform gaps that only ever matter in CI.

Each marker below is a real platform gap, not a bug to work around: the code
under test only ever runs inside the Linux sandbox container in production,
so Windows coverage of these specific behaviors is not meaningful.
"""

from __future__ import annotations

import sys

import pytest

# DEFAULT_SDK_HOME is the hardcoded POSIX path "/opt/band". pathlib only
# treats it as absolute under a POSIX flavour — WindowsPath("/opt/band")
# is relative.
requires_posix_absolute_paths = pytest.mark.skipif(
    sys.platform == "win32",
    reason="DEFAULT_SDK_HOME ('/opt/band') is only absolute under PosixPath",
)

# has_owner_only_permissions enforces POSIX mode bits (stat().st_mode & 0o077).
# Windows chmod/stat can't represent group/other bits — a non-read-only file
# always reports 666 — so the guard always fires there regardless of the
# requested mode.
requires_posix_permission_bits = pytest.mark.skipif(
    sys.platform == "win32",
    reason="has_owner_only_permissions needs real POSIX mode bits",
)

# grep -ralF targets the Linux sandbox shell. Windows runners have no reliable
# POSIX bash/grep on PATH — "bash" resolves to the WSL launcher stub, which
# isn't installed there.
requires_posix_shell = pytest.mark.skipif(
    sys.platform == "win32",
    reason="grep -ralF needs a real POSIX bash/grep, not the WSL launcher stub",
)
