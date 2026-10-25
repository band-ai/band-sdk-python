---
name: band-desktop-setup
description: "Set up, upgrade, or troubleshoot Claude Desktop as a Band agent. Use when installing or wiring agent-scope band-mcp and band-room-view, locating an agent key, configuring a self-hosted platform, replacing a standalone agent process with Desktop, or diagnosing Desktop presence, widgets, unanswered mentions, or room synchronization."
---

# Band Desktop Setup

Use the current package entry points in `pyproject.toml`, configuration in
`src/band/integrations/desktop_app/settings.py`, and implementation under
`src/band/integrations/desktop_app/` as the source of truth. Check command
behavior in its current implementation; use `--help` only where that entry
point supports it.

## Safety and scope

- Never print, request, or copy an agent key into chat. The user enters it
  directly in their Desktop configuration.
- Treat configuration edits as user-owned. Merge MCP server entries; do not
  replace the whole configuration file.
- Desktop is macOS-only. State that constraint before attempting setup.
- One agent key has one consumer. Before assigning it to Desktop, identify
  other processes that use it and ask the user to stop them.
- Ask before disabling a legacy plugin or changing an existing MCP entry.

## Workflow

1. Establish the user's goal: first installation, self-hosted configuration,
   upgrade, or diagnosis. Confirm Desktop's supported platform from the current
   implementation and package metadata.
2. Inspect only the local state needed for that goal: executable paths,
   existing Desktop MCP entries, and non-secret environment names. Do not read
   or echo credentials.
3. Apply the current package entry points and settings, preserving unrelated
   configuration. If an action would stop another process, disable a plugin,
   or overwrite an existing entry, get explicit approval first.
4. Verify the installed processes, room presence, and room view using the
   behavior covered in `tests/integrations/desktop_app/`. For a live mention
   check, make clear that Claude answers without a new user turn only while its
   watching loop is active. Watching holds a tool call open, so new user input
   waits until that call yields, bounded by its configured event timeout.
5. Report what was verified and any remaining boundary (for example, a missing
   agent key, a required Desktop restart, or an unavailable second participant).

For behavior after setup, inspect the room view implementation and its settings
before recommending changes. The room view's logs and tuning settings are owned
by `src/band/integrations/desktop_app/logs.py` and `settings.py`.
