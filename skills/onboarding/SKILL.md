---
name: thenvoi-onboarding
description: Scaffold a Tom & Jerry agent pair on the Thenvoi platform from credentials supplied in the conversation. Use when a user pastes onboarding text from band.ai containing Tom/Jerry agent IDs and API keys.
---

# Thenvoi Onboarding Skill

You are running the Thenvoi onboarding flow. The user has pasted text containing platform credentials and a link to this skill. Your job is to scaffold a working Tom & Jerry agent pair locally **by fetching the live example files from this same repo and transforming them** — this skill ships no copies of agent code.

## Source of truth

All agent code lives in `examples/` in this repo. Fetch via raw GitHub URLs:

```
https://raw.githubusercontent.com/thenvoi/thenvoi-sdk-python/<branch>/<path>
```

**Determining the branch:** parse it from the SKILL.md URL the user gave you. The expected shape is `https://raw.githubusercontent.com/thenvoi/thenvoi-sdk-python/<branch>/skills/onboarding/SKILL.md` — `<branch>` is the segment between the repo name and `skills/`.

- If you fetched SKILL.md from a `raw.githubusercontent.com` URL matching that shape, use the branch from the URL for all subsequent fetches.
- If SKILL.md was loaded via any other path (a local file, a different host, a URL that doesn't match the expected shape), **STOP and ask the user which branch to fetch examples from** — show them what URL you were given and ask. Do not silently fall back to `main` — a silent fallback can pull example files that don't match the SKILL.md the user is following, and the failure mode (404 or a subtly different example) is hard to debug.

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

## Procedure

Follow these steps in order. Use AskUserQuestion for every user prompt.

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

`AskUserQuestion` accepts at most 4 options, so we split the adapter pick into two questions. The second is only asked when the first selects the "framework" bucket.

**Step 2a — Ask the runtime category** (single-select, 4 options):

- **Framework + your LLM key** — Run inside an agent framework (langgraph / crewai / pydantic_ai). You provide an OpenAI or Anthropic API key.
- **Direct Anthropic SDK** — Plain Anthropic SDK loop. Needs an Anthropic API key. *(sets `adapter = anthropic`)*
- **Claude Agent SDK** — Uses the Claude Code subprocess. **No external LLM key needed** — requires Node.js 20+ and `npm install -g @anthropic-ai/claude-code`. *(sets `adapter = claude_sdk`)*
- **Parlant** — Conversation-modeling framework. Needs an OpenAI API key. *(sets `adapter = parlant`)*

**Step 2b — Ask the framework** *(only if Step 2a was "Framework + your LLM key")* (single-select, 3 options):

- **langgraph** — Graph-based, LangChain ecosystem.
- **crewai** — Role-based multi-agent.
- **pydantic_ai** — Pydantic AI agent.

### Step 3 — Ask which LLM (conditional)

| Adapter | LLM question | LLM env var |
|---|---|---|
| `anthropic` | Skip — Anthropic only | `ANTHROPIC_API_KEY` |
| `claude_sdk` | Skip — no external LLM | none |
| `parlant` | Skip — OpenAI only (default NLP service) | `OPENAI_API_KEY` |
| `crewai`, `langgraph`, `pydantic_ai` | Ask: OpenAI or Anthropic | matching key |

### Step 4 — Confirm output directory

Default to `./tom-jerry-agents/` in the cwd. Ask only if it already exists.

### Step 5 — Fetch and transform

Fetch these three files from the branch determined at the top:

1. `examples/prompts/characters.py`
2. The Tom file for the chosen adapter (see map above)
3. The Jerry file for the chosen adapter

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
# Agent credentials from the Thenvoi platform.
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

AskUserQuestion (single-select):
- **I'll add it to the .env myself** — show the path `<out>/.env` and which line to fill.
- **Add it for me now** — ask for the key and edit the .env in place.

### Step 8 — Ask who runs the agents

AskUserQuestion (single-select):
- **I'll run them myself** — show:
  ```
  cd <out>
  uv sync
  uv run python tom_agent.py     # terminal 1
  uv run python jerry_agent.py   # terminal 2
  ```
- **Claude, run them for me** — do these in order:
  1. `cd <out> && uv sync` (foreground, wait for it to finish). This installs deps and lockfile; the background launches below assume it succeeded.
  2. Start Tom with `run_in_background: true`: `cd <out> && uv run python tom_agent.py`
  3. Start Jerry with `run_in_background: true`: `cd <out> && uv run python jerry_agent.py`
  4. Give the user the `BashOutput` command for each background bash ID so they can tail logs.

### Step 9 — Show the user how to trigger the chase

Both agents are running and connected. To see them in action, present these steps to the user (copy the block below verbatim, just substitute the platform URL from `THENVOI_REST_URL` in their `.env`):

> **Watch Tom chase Jerry on the platform**
>
> 1. Open **<THENVOI_REST_URL>** in your browser and sign in.
> 2. In the left sidebar, click **Chats**.
> 3. Click **Start Your First Chat** — a new session opens.
> 4. In the **Participants** panel on the right, click the **+** next to the heading.
> 5. Select the **Tom** agent card, then click **Done**. Tom appears under **AGENTS** in the panel.
> 6. In the message box at the bottom, type `@Tom catch jerry` and hit send.
>
> Tom will look up Jerry, invite him into the chat automatically, and start trying to lure him out of his hole. The persuasion will escalate over up to 10 attempts — watch the back-and-forth in the chat, and tail the terminal logs (or the BashOutput stream if Claude is running them) if anything looks stuck.

Only add Tom as a participant — Tom finds and invites Jerry himself via the platform tools. Don't tell the user to add Jerry manually.

## Rules for Claude

- **Don't clone `thenvoi-sdk-python`.** Only fetch the specific files listed above.
- **Don't bundle copies of the examples** in this skill — fetch them live, every time.
- **Don't walk the user through `characters.py`** — it's long and not relevant to onboarding.
- **If a transformation pattern fails to match**, that's usually fine (the example already changed in a compatible way). If the *structure* looks unfamiliar (new imports you don't recognize, the agent class name changed, the `Agent.from_config` shape is different), STOP and surface what's odd — don't fabricate a fix.
- **Respect existing files.** If `<out>/` is non-empty, ask before overwriting.
- **Stop after step 9.** No refactors, no extra suggestions.
