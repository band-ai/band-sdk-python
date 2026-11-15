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
- Only one process should own the agent's live WebSocket session; a second
  runtime can supersede the first. The paired `band-mcp` process is expected
  to share the key for REST tools, so do not treat it as a competing session.
  Before configuring Desktop, identify any other live session owner and ask
  the user to stop it.
- Ask before disabling a legacy plugin or changing an existing MCP entry.
- If the legacy `band-peer@jam` plugin is present, check whether it currently
  claims Band room prompts; treat that as a possible conflict and ask before
  disabling it.

## Workflow

1. Establish the user's goal: first installation, self-hosted configuration,
   upgrade, or diagnosis. Confirm Desktop's supported platform from the current
   implementation and package metadata.
2. Inspect only the local state needed for that goal: executable paths,
   existing Desktop MCP entries, and non-secret environment names. Use
   `settings.py` to identify the default config path and supported overrides;
   do not read or echo credentials.
3. Resolve the `band-room-view` entry point and `desktop` extra from
   `pyproject.toml`; install `band-mcp` as a separate tool, then choose the
   released package or the local checkout:

   ```bash
   uv tool install band-mcp
   # Released package
   uv tool install --force "band-sdk[desktop]"
   # From the repository root, for an unreleased checkout
   uv tool install --force --editable ".[desktop]"
   ```

   Confirm the published package name and extra against `pyproject.toml`. A
   full Desktop restart is required after installation or config changes. Ask
   before restarting an open session because it stops another process.
4. Merge the two MCP entries into Desktop's existing configuration and use
   absolute executable paths because Desktop starts servers with a minimal
   `PATH`. Configure `band-mcp` with `--scope agent`, `BAND_AGENT_KEY`, and
   `BAND_BASE_URL`. The room view borrows the key and base URL from that entry;
   set `BAND_DESKTOP_MCP_SERVER` on the room-view entry when several entries
   qualify, or set `BAND_AGENT_KEY` and `BAND_BASE_URL` on the room view.
   `BAND_WS_URL` is optional.
   When omitted, the room view derives it from the base URL. If an action
   would stop another process, disable a plugin, or overwrite an existing
   entry, get explicit approval first.
5. Verify the live setup in Desktop: the agent is online and the room widget
   mounts. Use the tests in `tests/integrations/desktop_app/` as behavior
   references, not as proof that the installed processes work. On-demand is
   the default and checks the room at the start of a user's turn. Watching
   keeps a room-event call active so mentions can be answered without a new
   user turn; it uses model turns while quiet and holds new user input until the
   call yields, bounded by its configured event timeout. A live mention check
   requires watching to be active while no Claude turn is running. Inspect the
   widget's transport status and diagnostics; a disconnected WebSocket means
   the view is using its polling fallback. The view shows messages visible to
   that agent, so mention it in messages it needs to see.
6. For startup or transport failures, read the log path from
   `src/band/integrations/desktop_app/logs.py` and tuning names from
   `settings.py`. Report what was verified and any remaining boundary (for
   example, a missing key, required Desktop restart, or unavailable second
   participant).
