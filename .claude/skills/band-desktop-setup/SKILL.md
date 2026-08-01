---
name: band-desktop-setup
description: "Set up, upgrade, or troubleshoot Claude Desktop as a Band agent. Use when installing or wiring agent-scope band-mcp and band-room-view, locating an agent key, configuring a self-hosted platform, replacing a standalone agent process with Desktop, or diagnosing Desktop presence, widgets, unanswered mentions, or room synchronization."
---

# Band Desktop setup

The canonical setup, operating, verification, upgrade, and troubleshooting
guide is [docs/adapters/claude_desktop.md](../../../docs/adapters/claude_desktop.md).
Read it before taking action. Do not duplicate its commands, configuration,
or troubleshooting guidance here; keep this skill to the interactive setup
workflow.

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

1. Read the canonical guide and establish the user's goal: first installation,
   self-hosted configuration, upgrade, or diagnosis.
2. Inspect only the local state needed for that goal: executable paths,
   existing Desktop MCP entries, and non-secret environment names. Do not read
   or echo credentials.
3. Apply the guide's relevant steps, preserving unrelated configuration.
   If an action would stop another process, disable a plugin, or overwrite an
   existing entry, get explicit approval first.
4. Use the guide's verification sequence. For a live mention check, make clear
   that Claude can answer without a new user turn only while its watching loop
   is active.
5. Report what was verified and any remaining boundary (for example, a missing
   agent key, a required Desktop restart, or an unavailable second participant).

For behaviour after setup, direct the user to the guide rather than restating
it: joining, watching, room visibility, tuning, and troubleshooting all live
there.
