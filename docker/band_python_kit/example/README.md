# Example workspace

A complete, runnable workspace for the [Band Python kit](../README.md): an
echo agent you can copy and grow into a real one. Building the kit image and
creating the sandbox is the kit README's quickstart; this page covers the
workspace itself.

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

Workspace edits apply live (the workspace is a mount); dependency changes
take effect at the next sandbox restart, when the launcher re-syncs the
lock. Kit-level changes need a recreate — see the
[kit README](../README.md#network-access).
