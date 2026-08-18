---
name: thenvoi-onboarding-cursor
description: Scaffold a Tom & Jerry agent pair on the Band.ai platform from credentials supplied in the conversation. Cursor-flavored variant of the Thenvoi onboarding skill.
---

# Band.ai Onboarding Skill (Cursor)

You are running the Band.ai onboarding flow inside Cursor (Agent / Composer mode). The user pasted text containing platform credentials and a link to this skill. Your job is to scaffold a working Tom & Jerry agent pair locally **by fetching the live example files from the `thenvoi-sdk-python` repo and transforming them** — this skill ships no copies of agent code.

This is the Cursor variant of the skill. Sibling files: `SKILL.md` (Claude Code), `codex.md` (Codex).

## Source of truth

All agent code lives in `examples/` in the `thenvoi/thenvoi-sdk-python` GitHub repo. Fetch raw files with `curl` from the terminal:

```
https://raw.githubusercontent.com/thenvoi/thenvoi-sdk-python/<branch>/<path>
```

**Determining the branch:** parse it from the URL of this file the user gave you. The expected shape is `https://raw.githubusercontent.com/thenvoi/thenvoi-sdk-python/<branch>/skills/onboarding/cursor.md` — `<branch>` is the segment between the repo name and `skills/`.

- If you fetched this file from a `raw.githubusercontent.com` URL matching that shape, use the branch from the URL for all subsequent fetches.
- If this file was loaded via any other path (a local file, a different host, a URL that doesn't match the expected shape), **STOP and ask the user which branch to fetch examples from** — show them what URL you were given and ask. Do not silently fall back to `main` — a silent fallback can pull example files that don't match this skill, and the failure mode (404 or a subtly different example) is hard to debug.

### Example file map (adapter → Tom + Jerry filenames)

| Adapter | Tom file | Jerry file |
|---|---|---|
| `langgraph` | `examples/langgraph/07_tom_agent.py` | `examples/langgraph/08_jerry_agent.py` |
| `crewai` | `examples/crewai/05_tom_agent.py` | `examples/crewai/06_jerry_agent.py` |
| `anthropic` | `examples/anthropic/03_tom_agent.py` | `examples/anthropic/04_jerry_agent.py` |
| `claude_sdk` | `examples/claude_sdk/03_tom_agent.py` | `examples/claude_sdk/04_jerry_agent.py` |
| `parlant` | `examples/parlant/04_tom_agent.py` | `examples/parlant/05_jerry_agent.py` |
| `pydantic_ai` | `examples/pydantic_ai/03_tom_agent.py` | `examples/pydantic_ai/04_jerry_agent.py` |

The shared character prompts live at `examples/prompts/characters.py` — fetch verbatim, no transformation.

## How to ask the user questions

Cursor's Agent mode has no structured picker UI for free-form questions. For every user prompt below, write a chat message containing a numbered list of options and wait for the user to reply with a number or the label. Don't proceed until they answer. Don't combine multiple questions into one prompt — ask, wait, then ask the next.

## Procedure

Follow these steps in order.

### Step 1 — Extract credentials from the pasted text

Look in the conversation so far for:
- A **Tom agent ID** (UUID) and **Tom API key**
- A **Jerry agent ID** (UUID) and **Jerry API key**
- Optionally `THENVOI_REST_URL` and `THENVOI_WS_URL`. If absent, use the production defaults:
  - `THENVOI_REST_URL=https://app.band.ai`
  - `THENVOI_WS_URL=wss://app.band.ai/api/v1/socket/websocket`

(The env var names stay `THENVOI_*` because the SDK reads those names — only the URL values point at band.ai.)

If any of the four required credentials are missing, ask the user for them before proceeding.

### Step 2 — Ask which adapter (two-stage picker)

The adapter pick is split into two questions for clarity. The second is only asked when the first selects the "framework" bucket.

**Step 2a — Ask the runtime category** (numbered list, single choice):

> Which runtime do you want?
> 1) **Framework + your LLM key** — Run inside an agent framework (langgraph / crewai / pydantic_ai). You provide an OpenAI or Anthropic API key.
> 2) **Direct Anthropic SDK** — Plain Anthropic SDK loop. Needs an Anthropic API key. *(sets `adapter = anthropic`)*
> 3) **Claude Agent SDK** — Uses the Claude Code subprocess. **No external LLM key needed** — requires Node.js 20+ and `npm install -g @anthropic-ai/claude-code`. *(sets `adapter = claude_sdk`)*
> 4) **Parlant** — Conversation-modeling framework. Needs an OpenAI API key. *(sets `adapter = parlant`)*

**Step 2b — Ask the framework** *(only if Step 2a was option 1)*:

> Which framework?
> 1) **langgraph** — Graph-based, LangChain ecosystem.
> 2) **crewai** — Role-based multi-agent.
> 3) **pydantic_ai** — Pydantic AI agent.

### Step 3 — Ask which LLM (conditional)

| Adapter | LLM question | LLM env var |
|---|---|---|
| `anthropic` | Skip — Anthropic only | `ANTHROPIC_API_KEY` |
| `claude_sdk` | Skip — no external LLM | none |
| `parlant` | Skip — OpenAI only (default NLP service) | `OPENAI_API_KEY` |
| `crewai`, `langgraph`, `pydantic_ai` | Ask: OpenAI or Anthropic (numbered list) | matching key |

### Step 4 — Confirm output directory

Default to `./tom-jerry-agents/` in the cwd. Ask only if it already exists.

### Step 5 — Fetch and transform

Fetch these three files using `curl -fsSL` in the terminal:

1. `examples/prompts/characters.py`
2. The Tom file for the chosen adapter (see map above)
3. The Jerry file for the chosen adapter

Example:
```bash
BRANCH="<branch>"
ADAPTER="<adapter>"
curl -fsSL "https://raw.githubusercontent.com/thenvoi/thenvoi-sdk-python/$BRANCH/examples/prompts/characters.py"
```

**For `characters.py`**: write it verbatim to `<out>/characters.py`. No transformation.

**For each agent file**, apply the transformations below. They are pattern-based. If a pattern doesn't match (e.g. the example was already cleaned up), skip silently — that's fine. If something looks structurally different from what's documented here (e.g. a new shared import you don't recognize), STOP and tell the user before writing — don't guess.

#### Transformations to apply

1. **Strip PEP 723 inline script header.** Remove the entire block from `# /// script` to `# ///` inclusive (it's at the top of the file).

2. **Drop the sys.path hack.** Remove the line:
   ```python
   sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
   ```
   Also remove the `import sys` line if it's no longer used elsewhere in the file.

3. **Rewrite the characters import.** Change:
   ```python
   from prompts.characters import generate_tom_prompt   # or generate_jerry_prompt
   ```
   to:
   ```python
   from characters import generate_tom_prompt
   ```

4. **Replace the logging setup.** Change:
   ```python
   from setup_logging import setup_logging
   ...
   setup_logging()
   ```
   to:
   ```python
   import logging
   logging.basicConfig(level=logging.INFO)
   ```
   (Keep the existing `logger = logging.getLogger(__name__)` line.)

5. **Replace the module docstring.** The upstream example docstring describes how to run the file from the repo root and mentions `prompts/characters.py` — both are wrong for the scaffolded standalone project. Replace the entire top-of-file `"""..."""` block (the one immediately after `from __future__ import annotations`, if present, or otherwise the first triple-quoted string in the file) with a single-line docstring:

   - Tom file: `"""Tom the cat agent (<ADAPTER>)."""`
   - Jerry file: `"""Jerry the mouse agent (<ADAPTER>)."""`

   Where `<ADAPTER>` is the human-readable adapter name (LangGraph, CrewAI, Anthropic, Claude SDK, Parlant, Pydantic AI).

6. **Swap the LLM if the user picked something other than the example default.** The example defaults are listed below. Only modify if the user's choice differs.

   | Adapter | Example default | Anthropic swap | OpenAI swap |
   |---|---|---|---|
   | `langgraph` | `from langchain_openai import ChatOpenAI` + `ChatOpenAI(model="gpt-5.4-mini")` | replace import with `from langchain_anthropic import ChatAnthropic`, replace constructor with `ChatAnthropic(model="claude-sonnet-4-5-20250929")` | already default |
   | `crewai` | `model="gpt-5.4-mini"` | `model="anthropic/claude-sonnet-4-5-20250929"` (litellm prefix) | already default |
   | `pydantic_ai` | `model="openai:gpt-5.4-mini"` | `model="anthropic:claude-sonnet-4-5-20250929"` | already default |
   | `anthropic`, `claude_sdk`, `parlant` | n/a — no swap path | — | — |

Write each transformed file to `<out>/tom_agent.py` and `<out>/jerry_agent.py`.

### Step 6 — Generate scaffolding files

Write these directly (they're scaffolding, not SDK code — no fetch needed). Substitute the bracketed values.

**`<out>/agent_config.yaml`**
```yaml
# Agent credentials from the Band.ai platform.
tom_agent:
  agent_id: "<TOM_AGENT_ID>"
  api_key: "<TOM_API_KEY>"

jerry_agent:
  agent_id: "<JERRY_AGENT_ID>"
  api_key: "<JERRY_API_KEY>"
```

**`<out>/.env`** — use the REST/WS URLs from step 1, default to production if absent. The third line depends on the LLM choice:
- OpenAI → `OPENAI_API_KEY=`
- Anthropic (incl. the `anthropic` adapter) → `ANTHROPIC_API_KEY=`
- `claude_sdk` → omit entirely
- `parlant` → `OPENAI_API_KEY=`

```
THENVOI_REST_URL=<REST_URL>
THENVOI_WS_URL=<WS_URL>
<LLM_KEY_LINE>
```

**`<out>/pyproject.toml`** — `<EXTRA>` is the adapter name with two hyphen-form exceptions: `claude_sdk` → `claude-sdk`, `pydantic_ai` → `pydantic-ai`. The other adapters (`langgraph`, `crewai`, `anthropic`, `parlant`) use their name verbatim.

The `requires-python` upper bound matters: without it `uv` will pick the newest Python on the machine (3.14+ exists at time of writing), and pydantic-core's PyO3 currently caps at 3.13 — `uv sync` will fail to build. Cap at `<3.14`.

`<ANTHROPIC_DEPS>` is normally empty, but for crewai + Anthropic and pydantic_ai + Anthropic some extras don't get pulled in via the band-sdk extras and need to be added explicitly:

| Adapter + LLM | `<ANTHROPIC_DEPS>` content (a single line, indented to match) |
|---|---|
| `crewai` + Anthropic | `    "crewai[anthropic]==1.14.3",` |
| `pydantic_ai` + Anthropic | `    "pydantic-ai-slim[anthropic]>=1.56.0",` |
| Anything else | *(omit the line entirely)* |

```toml
[project]
name = "thenvoi-tom-jerry"
version = "0.1.0"
requires-python = ">=3.11,<3.14"
dependencies = [
    "band-sdk[<EXTRA>]>=0.2.10",
<ANTHROPIC_DEPS>
    "python-dotenv>=1.0.0",
]
```

Note: `band-sdk` is the PyPI name; the installed Python module is still `thenvoi` (so the agent code's `from thenvoi import ...` imports are unchanged).

**`<out>/.python-version`**
```
3.12
```

(Belt-and-suspenders with the `requires-python` cap — pins the interpreter explicitly so `uv sync` picks 3.12 instead of whatever the newest local Python happens to be.)

**`<out>/.gitignore`**
```
.env
agent_config.yaml
.venv/
__pycache__/
```

### Step 7 — Ask how to handle the LLM API key

Skip for `claude_sdk`.

Ask the user (numbered list):

> How should we handle your LLM API key?
> 1) **I'll add it to the .env myself** — I'll point you at `<out>/.env` and which line to fill.
> 2) **Add it for me now** — paste the key in your next message and I'll write it into `.env`.

### Step 8 — Ask who runs the agents

Ask the user (numbered list):

> How should we run the two agents?
> 1) **I'll run them myself** (recommended — easier to watch logs and shut down)
> 2) **You run them for me in the background**

**If option 1:**

Show this and stop. The user runs the commands in two terminals.

```
cd <out>
uv sync
uv run python tom_agent.py     # terminal 1
uv run python jerry_agent.py   # terminal 2
```

**If option 2:**

Cursor's Agent terminal doesn't keep long-lived processes alive after the agent step ends, so run the agents detached via `nohup` and capture PIDs. Do these in order:

1. `cd <out> && uv sync` (foreground, wait for it to finish; abort the rest on non-zero exit).
2. Launch Tom detached:
   ```bash
   cd <out> && nohup uv run python tom_agent.py > tom.log 2>&1 & echo "TOM_PID=$!"
   ```
3. Launch Jerry detached:
   ```bash
   cd <out> && nohup uv run python jerry_agent.py > jerry.log 2>&1 & echo "JERRY_PID=$!"
   ```
4. Tell the user how to tail logs and how to kill the processes when they're done:
   ```bash
   tail -f <out>/tom.log <out>/jerry.log    # watch
   kill <TOM_PID> <JERRY_PID>               # stop
   ```

### Step 9 — Show the user how to trigger the chase

Both agents are running and connected. Present these steps to the user (copy the block below verbatim, just substitute the platform URL from `THENVOI_REST_URL` in their `.env`):

> **Watch Tom chase Jerry on the platform**
>
> 1. Open **<THENVOI_REST_URL>** in your browser and sign in.
> 2. In the left sidebar, click **Chats**.
> 3. Click **Start Your First Chat** — a new session opens.
> 4. In the **Participants** panel on the right, click the **+** next to the heading.
> 5. Select the **Tom** agent card, then click **Done**. Tom appears under **AGENTS** in the panel.
> 6. In the message box at the bottom, type `@Tom catch jerry` and hit send.
>
> Tom will look up Jerry, invite him into the chat automatically, and start trying to lure him out of his hole. The persuasion will escalate over up to 10 attempts — watch the back-and-forth in the chat, and tail the terminal logs if anything looks stuck.

Only add Tom as a participant — Tom finds and invites Jerry himself via the platform tools. Don't tell the user to add Jerry manually.

## Rules for the agent

- **Don't clone `thenvoi-sdk-python`.** Only fetch the specific files listed above.
- **Don't bundle copies of the examples** in this skill — fetch them live, every time.
- **Don't walk the user through `characters.py`** — it's long and not relevant to onboarding.
- **If a transformation pattern fails to match**, that's usually fine (the example already changed in a compatible way). If the *structure* looks unfamiliar (new imports you don't recognize, the agent class name changed, the `Agent.from_config` shape is different), STOP and surface what's odd — don't fabricate a fix.
- **Respect existing files.** If `<out>/` is non-empty, ask before overwriting.
- **Stop after step 9.** No refactors, no extra suggestions.
