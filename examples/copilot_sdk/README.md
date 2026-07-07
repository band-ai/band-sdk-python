# GitHub Copilot SDK Examples for Band

Examples of using the [GitHub Copilot SDK](https://github.com/github/copilot-sdk)
(`github-copilot-sdk`) with the Band platform.

## Prerequisites

### 1. GitHub Copilot access

The SDK manages the Copilot CLI runtime internally. It needs a GitHub
account **with Copilot access** (an authenticated account without a Copilot
subscription fails at model-call time, not at login):

```bash
# Either authenticate the GitHub CLI locally...
gh auth login

# ...or provide a token via the environment
export GITHUB_TOKEN=ghp_...
```

Auth resolves automatically: the token wins when set, otherwise the
logged-in GitHub user is used — no adapter flags needed.

### 2. Pre-fetch the runtime (recommended)

The CLI runtime is auto-downloaded on first use; pre-fetch it so agent
startup is instant:

```bash
python -m copilot download-runtime
```

### 3. Python dependencies

```bash
# Install with copilot_sdk extras (requires Python 3.11+)
uv sync --extra copilot_sdk
# or: pip install "band-sdk[copilot_sdk]"
```

### 4. Agent credentials

Add a `copilot_sdk_agent` entry (and `tom_agent`/`jerry_agent` for the
character examples) with `agent_id` and `api_key` to `agent_config.yaml` (copy `agent_config.yaml.example`)
**in the repo root** — the loader resolves it from the current working
directory — and set `BAND_WS_URL` / `BAND_REST_URL` in `.env`.

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal Copilot agent that replies in Band rooms |
| `02_byok_anthropic.py` | BYOK: inference on your own Anthropic key |
| `03_tom_agent.py` | Tom character agent |
| `04_jerry_agent.py` | Jerry character agent |
| `05_contact_and_memory_agent.py` | Contact + memory platform tools |
| `06_ask_user.py` | Human-in-the-loop: `ask_user` questions answered in the room |

```bash
# Run from the repo root (agent_config.yaml is resolved from the CWD)
uv run examples/copilot_sdk/01_basic_agent.py
```

## Feature flags

Event reporting and platform-tool exposure are **off by default** — opt in
via `AdapterFeatures`:

```python
features=AdapterFeatures(
    emit={Emit.EXECUTION, Emit.THOUGHTS},          # tool_call/thought events
    capabilities={Capability.MEMORY, Capability.CONTACTS},  # band_* tool groups
)
```

## Human-in-the-loop (`ask_user`)

Copilot's built-in `ask_user` tool lets the model ask a human a question —
freeform or multiple-choice. Off by default; route it with `ask_user`:

```python
config = CopilotSDKAdapterConfig(ask_user="room")   # ask the people in the room
config = CopilotSDKAdapterConfig(ask_user=handler)  # ask someone outside it
```

### `ask_user="room"` — ask the room (recommended)

The question posts into the room (mentioning whoever triggered the turn),
the turn ends, and the answer arrives as the next room message — picked up
by the same persisted Copilot session, which still holds the pending
question in its history. `06_ask_user.py` demonstrates it.

The turn must end when the question posts: Band delivers a room's messages
one at a time, so a turn blocked waiting for the reply could never receive
it — and Copilot keeps an unanswered `ask_user` pending forever (no
timeout, not replayed on resume). Room mode therefore never blocks the
room, never races `turn_timeout_s`, and survives restarts.

### `ask_user=<handler>` — ask an operator

A callable is awaited mid-turn with `(UserInputRequest, {"session_id"})`
and returns `{"answer", "wasFreeform"}`. The turn stays open while it
waits, so `turn_timeout_s` must exceed the handler's answer window (the
defaults do: the console gives up gracefully at 90s, under the 120s turn
default). Tell the model the operator exists — handler mode injects no
prompt guidance — and raise both knobs for patient operators:

```python
config = CopilotSDKAdapterConfig(
    custom_section=(
        "A human operator supervises you. When a request needs a decision "
        "you cannot make alone, consult them with the ask_user tool."
    ),
    ask_user=OperatorConsole(answer_timeout_s=300.0).ask,
    turn_timeout_s=600.0,  # must stay above answer_timeout_s
)
```

Prefer `OperatorConsole` (`band.integrations.copilot_sdk`) over a bare
`input()` handler — the SDK leaves the edge cases to the host, and the
console covers them: per-question deadline (`answer_timeout_s`), answer
validation against `choices`/`allowFreeform`, one prompt owning the
terminal across concurrent rooms, parked log output while a prompt is
open, and a graceful "operator unavailable" answer on stdin EOF.

### One process per agent

The runtime refuses to start a second process for the same agent id on
one host (duplicates steal in-flight room messages and resume the same
on-disk Copilot sessions). Opt out with `AgentConfig(single_instance=False)`.

## Models

- `model=None` (default) uses the Copilot CLI's default model. List what
  your account can use with `await client.list_models()`.
- With **BYOK** (`provider=...`) the `model` names the *provider's* model
  (e.g. `claude-haiku-4-5` for Anthropic) — not a Copilot model id, and `base_url` is required. BYOK moves
  inference billing to your key; GitHub auth is still required to boot the
  Copilot runtime.

## Notes

- Each Band room maps to its own Copilot session (`band-{agent-id}-{room_id}`),
  so conversations stay isolated — per room *and* per agent — and can resume
  across restarts on the same host.
- Copilot's built-in shell/file tools are disabled by the adapter; the model
  only sees Band platform tools (and any custom tools you add via
  `additional_tools`).
- Agents sharing a host or one `CopilotClient` (`client=`) are kept apart by
  their names in the default session ids; set `session_id_prefix` only to
  override that scheme, and use per-agent `base_directory` for full on-disk
  state isolation.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: Config file not found at .../agent_config.yaml` | Example run from a subdirectory (config resolves from CWD) | Run from the repo root |
| `BandConfigError: Not authenticated with GitHub Copilot` at startup | No `gh auth login` and no `GITHUB_TOKEN` | Log in with the GitHub CLI or set a token |
| Turns fail with model-call errors despite valid login | Account has no Copilot entitlement | Use an account with a Copilot subscription, or BYOK |
| BYOK turns fail: `Session error: Failed to get response from the AI model … 429 You exceeded your current quota` | Provider key out of quota or invalid | Fund/replace the provider key, or drop `provider=` to use the Copilot subscription |
| Agent startup is slow | Runtime downloading/spawning at boot | Pre-fetch with `python -m copilot download-runtime` |
| Turn raises after `turn_timeout_s` (default 120s) | Long-running turn | Raise `CopilotSDKAdapterConfig(turn_timeout_s=...)` |
| Agent replies but no tool/thought events appear | `Emit` flags not set | Pass `features=AdapterFeatures(emit={...})` |
| `BandConfigError: … already running on this host` at startup | Another process runs the same agent id | Stop it, or set `AgentConfig(single_instance=False)` |
| Room reply says the operator did not answer (console handler) | `ask_user` question expired unanswered | Answer within `answer_timeout_s`, or raise it (keep it below `turn_timeout_s`) |
| Every question answers "no operator is attached" (console handler) | stdin closed (headless run / piped input exhausted) | Run in a real terminal, or use `ask_user="room"` |
