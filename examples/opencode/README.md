# OpenCode Examples

These examples run a Band agent through an [OpenCode](https://opencode.ai/)
server. Each Band room maps to an OpenCode session, so concurrent rooms keep
their own working context.

## Prerequisites

1. Install OpenCode: `npm install -g opencode-ai`.
2. Give the server a provider key. The defaults (`OPENCODE_PROVIDER_ID=opencode`
   with `OPENCODE_MODEL_ID=mimo-v2.5-free`) are [OpenCode
   Zen](https://opencode.ai/docs/zen/)-hosted, so the server needs a Zen API key
   or every prompt fails. Either run `opencode auth login` and pick OpenCode Zen,
   or write the key into the server's config:

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "provider": {
       "opencode": { "options": { "apiKey": "{env:OPENCODE_ZEN_API_KEY}" } }
     }
   }
   ```

   Place it at `~/.config/opencode/opencode.json`, or inside the directory you
   serve from (see the next step) — OpenCode honours a cwd-local
   `opencode.json` on every platform. `{env:...}` substitutes from the server
   process's environment, so export `OPENCODE_ZEN_API_KEY` before serving. To
   use a different provider, set `OPENCODE_PROVIDER_ID` and `OPENCODE_MODEL_ID`
   to one the server is authenticated for (`opencode models` lists them).
3. Start the server **from an empty throwaway directory**:

   ```bash
   cd "$(mktemp -d)" && opencode serve --hostname=127.0.0.1 --port=4096
   ```

   OpenCode is a coding agent with shell, read, and grep tools. Only
   `02_workspace_agent.py` sets a directory; the others inherit the server's
   working directory, and inside a source checkout a small model tends to
   explore the code instead of replying. An empty cwd keeps it on task.
4. Add the selected agent's credentials to `agent_config.yaml`.
5. Set the Band platform URLs:

   ```bash
   export BAND_WS_URL=wss://your-band-host/api/v1/socket/websocket
   export BAND_REST_URL=https://your-band-host
   ```

The examples read the repository-root `.env` automatically, from any working
directory.

## Examples

| File | Demonstrates |
|---|---|
| `01_basic_agent.py` | The smallest OpenCode-backed Band agent. |
| `02_workspace_agent.py` | Scoping OpenCode to a local repository with manual permissions. |
| `03_custom_tools_agent.py` | Adding application tools through the adapter's local MCP server. |
| `04_memory_secretary.py` | Giving an OpenCode agent durable Band memory tools. |
| `05_tom_agent.py` | A Tom character agent for a multi-agent room. |
| `06_jerry_agent.py` | A Jerry character agent for a multi-agent room. |

Run one from the repository root:

```bash
uv run examples/opencode/03_custom_tools_agent.py
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_KEY` | `darter` | Entry in `agent_config.yaml` to run as. |
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | OpenCode server URL. |
| `OPENCODE_PROVIDER_ID` | `opencode` | Provider sent with each prompt. |
| `OPENCODE_MODEL_ID` | `mimo-v2.5-free` | Model sent with each prompt. |
| `OPENCODE_AGENT` | unset | Optional OpenCode agent profile. |
| `OPENCODE_DIRECTORY` | unset | Repository directory for the workspace example. |
| `OPENCODE_WORKSPACE` | unset | Optional OpenCode workspace selector. |
| `OPENCODE_APPROVAL_MODE` | `manual` | `manual`, `auto_accept`, or `auto_decline`. |

`02_workspace_agent.py` requires an absolute `OPENCODE_DIRECTORY`. In manual
mode, reply to a permission request in the Band room with `approve`, `always`,
or `reject`. `auto_accept` allows every OpenCode permission request, so only
use it for trusted, isolated automation.

`AGENT_KEY` applies to `01`-`04`. `05_tom_agent.py` and `06_jerry_agent.py`
ignore it and always run as `tom_agent` and `jerry_agent`, so the two can share
a room.

## Tom and Jerry

Add `tom_agent` and `jerry_agent` credentials to `agent_config.yaml`, start
`05_tom_agent.py` and `06_jerry_agent.py` in separate terminals, then invite
both to the same Band room. Their prompts come from the shared
`examples/prompts/characters.py` module, keeping the character behavior
consistent with the other adapter examples.
