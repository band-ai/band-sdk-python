# Echo-agent starter workspace

A complete, runnable starter workspace for the
[Band Python kit](../README.md): an echo agent you can copy and grow into a
real one. Building the kit image and creating the sandbox is the kit README's
quickstart; this page covers the workspace itself.

## Files

| File | What it is |
|---|---|
| `band.yaml` | Agent identity, endpoints, credentials opt-in, runtime paths — annotated, strict (unknown fields fail the launch) |
| `main.py` | The entrypoint the launcher execs — here an echo agent with graceful shutdown |
| `pyproject.toml` + `uv.lock` | A plain `uv` project. The committed lock is required — the launcher never resolves at startup |
| `secrets.env.example` | Template for the opt-in credential file |
| `.gitignore` | Keeps `.band/` (secrets) and stray venvs out of Git |

## Set your identity

Put your registered agent's id in `band.yaml` (`agent.id`), or export
`BAND_AGENT_ID`. Endpoints default to production; the annotated `band.yaml`
shows how to target another Band deployment.

## Credentials

Create the opt-in credential file from the template:

```bash
mkdir -p .band
cp secrets.env.example .band/secrets.env
chmod 600 .band/secrets.env    # the launcher rejects wider permissions
```

Fill in `BAND_API_KEY` and uncomment the LLM keys your agent uses. Only the
documented names in the template are accepted — anything else fails the
launch. Values already present in the process environment always win; the
file only fills gaps.

The launcher enforces the guardrails (gitignored, never Git-tracked,
owner-only, no symlinks), and `band.yaml`'s
`credentials.acknowledgePlaintextInSandbox: true` records that the plaintext
keys exist in both your workspace and the sandbox VM. Never commit `.band/`.

## Make it yours

`main.py`'s `EchoAdapter` echoes every message. Swap it for any
`band.adapters.*` framework adapter (LangGraph, Anthropic, CrewAI, ...), add
the matching `band-sdk` extra to `pyproject.toml`, and refresh the lock with
`uv lock`.

The whole change is three files. For Anthropic:

```diff
 # pyproject.toml — add the framework extra
-dependencies = ["band-sdk>=1.1.0"]
+dependencies = ["band-sdk[anthropic]>=1.1.0"]
```

```diff
 # main.py — swap the adapter (the EchoAdapter class and its imports can go)
-from band.core.simple_adapter import SimpleAdapter
+from band.adapters.anthropic import AnthropicAdapter
 ...
     agent = Agent.create(
-        adapter=EchoAdapter(),
+        adapter=AnthropicAdapter(system_prompt="You are a helpful Band agent."),
```

```diff
 # .band/secrets.env — provide the backend key the adapter reads
-# ANTHROPIC_API_KEY=
+ANTHROPIC_API_KEY=sk-ant-...
```

Then `uv lock` and restart the sandbox. Every framework follows the same
shape: the extra in `pyproject.toml`, the adapter in `main.py`, its key in
the secrets file (or environment).

Workspace edits apply live (the workspace is a mount); dependency changes
take effect at the next sandbox restart, when the launcher re-syncs the
lock. Kit-level changes need a recreate — see the
[kit README](../README.md#network-access).

## Start from a repository

Instead of shipping the project in the workspace, the launcher can clone
it at startup: keep only `band.yaml` (and `.band/secrets.env`) at the
workspace root, set `project.path` to a subdirectory (e.g. `app`), and
uncomment the annotated `repo:` block in `band.yaml` with your HTTPS
repository URL. The clone must contain the usual project shape
(`main.py`, `pyproject.toml`, committed `uv.lock`). On restart the
existing checkout is validated and reused, never re-cloned.
