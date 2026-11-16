"""The update banner `sbx` appends to stdout, shared by tests of its `--json` parsing."""

from __future__ import annotations

# Captured from sbx v0.47.0's stdout after `policy check network --json`, on the
# run where its daily update check found v0.53.0.
UPDATE_BANNER = (
    "\n"
    "╭──────────────────────────────────────────────────────────────────────────────────╮\n"
    "│ Docker Sandboxes Update Available                                                │\n"
    "├──────────────────────────────────────────────────────────────────────────────────┤\n"
    "│ v0.47.0  →  v0.53.0                                                              │\n"
    "├──────────────────────────────────────────────────────────────────────────────────┤\n"
    "│ Release notes  https://github.com/docker/sbx-releases/releases/tag/v0.53.0       │\n"
    "├──────────────────────────────────────────────────────────────────────────────────┤\n"
    "│ To upgrade     brew upgrade docker/tap/sbx                                       │\n"
    "╰──────────────────────────────────────────────────────────────────────────────────╯\n"
)
