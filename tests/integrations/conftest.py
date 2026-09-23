"""Collection rules for the deep framework integration tests."""

from __future__ import annotations

import sys

# The Desktop room view is a macOS Claude Desktop companion: its relay elects a
# leader with fcntl.flock and fans events to siblings over a Unix socket, and
# it reads Desktop's config from ~/Library. Windows has none of that, so
# importing those modules there raises during collection — which aborts the
# whole session rather than failing one test.
#
# Ignore the directory by path rather than by glob: collect_ignore_glob is
# fnmatched against the whole path, so a "desktop_app/*" pattern never matches
# the "desktop_app\..." pytest reports on Windows — the one platform it exists
# for. collect_ignore compares resolved paths, so it is separator-safe.
collect_ignore = ["desktop_app"] if sys.platform == "win32" else []
