# Echo-agent starter workspace

A complete `uv` workspace for the [Band Python workload](../README.md).
Follow its [published echo quickstart](../README.md#published-echo-quickstart)
for ordered host setup, scoped credentials, creation, one owned PTY, a visible
echo reply and cleanup. The echo agent needs no LLM key or paid inference.

## Files and identity

| File | Purpose |
|---|---|
| `band.yaml` | Schema 1 identity, endpoints, credential mode and runtime paths |
| `main.py` | Echo adapter and shutdown handling for delivered signals |
| `pyproject.toml`, `uv.lock` | Customer dependencies; the committed lock is required |
| `secrets.env.example` | Optional plaintext credential template |
| `.gitignore` | Keeps `.band/` and virtualenvs out of Git |

Set `agent.id` in `band.yaml` to a registered agent id. The optional
[host provisioning route](../README.md#self-registration-no-pre-provisioned-agent)
can register an agent and fill this field using an entitled user key.
Unknown configuration fields fail startup. The mounted workspace can be at
any host path; do not replace the sandbox-owned runtime paths with mount paths.

## Credentials

The default `credentials.source: proxy-managed` uses a host-side secret and a
nonsecret sentinel in the VM. Follow the canonical
[credential custody instructions](../README.md#credential-custody), including
stdin transfer, unique ownership and secret-before-create ordering. No global
provider binding is assumed for this v3 workload.

For the opt-in plaintext fallback, change the credential block as annotated
in `band.yaml`, then create the file:

```bash
mkdir -p .band
cp secrets.env.example .band/secrets.env
chmod 600 .band/secrets.env
```

This puts real keys in the workspace and VM and produces a launcher warning.
The launcher requires the file to be gitignored, never Git-tracked, owner-only
and not a symlink. Only documented names are accepted; existing environment
values win. Never commit `.band/`.

## Optional Anthropic conversion

This route uses paid provider inference and needs your own Anthropic API key
and `uv` installed on the host to update the customer lock.
It is an acceptance recipe: verify a completed model reply before considering
it successful. Missing provider credentials, entitlement or quota block that
check. Keep the same `VERSION`, `SANDBOX`, `WORKSPACE`, `WORKLOAD` and
`BAND_SECRET_HOST` variables from the quickstart.

Stop the sandbox and close its old owned PTY as documented in the quickstart.
In `$WORKSPACE/pyproject.toml`, change only the SDK extra; retain the actual
starter SDK floor and settings requirement:

```diff
-dependencies = ["band-sdk>=1.4.2", "pydantic-settings>=2.15.0"]
+dependencies = ["band-sdk[anthropic]>=1.4.2", "pydantic-settings>=2.15.0"]
```

Replace `main.py` with the complete application below. `custom_section` retains
the adapter's rendered Band instructions; an explicit `system_prompt` would
replace them. The settings, identity/endpoints and graceful handling of delivered
signals remain part of the application:

```python notest
from __future__ import annotations

import asyncio

from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters.anthropic import AnthropicAdapter, AnthropicAdapterConfig
from band.runtime.shutdown import run_with_graceful_shutdown


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAND_",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )
    agent_id: str
    api_key: str
    ws_url: str
    rest_url: str


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    anthropic_proxy_key: str


async def main() -> None:
    settings = AgentSettings()
    agent = Agent.create(
        adapter=AnthropicAdapter(
            AnthropicAdapterConfig(
                provider_key=ProviderSettings().anthropic_proxy_key,
                custom_section="Reply briefly and clearly.",
            )
        ),
        agent_id=settings.agent_id,
        api_key=settings.api_key,
        ws_url=settings.ws_url,
        rest_url=settings.rest_url,
    )
    await run_with_graceful_shutdown(agent)


if __name__ == "__main__":
    asyncio.run(main())
```

Refresh the actual customer lock on the host. Remove the stopped sandbox but
retain its owned Band secret. Store the provider secret **before recreating**
so `ANTHROPIC_PROXY_KEY` is available at launch. sbx reserves
`ANTHROPIC_API_KEY`; the application reads the nonreserved variable and passes
its sentinel explicitly as the adapter's `provider_key`. The `sk-ant-` placeholder is a
nonsecret provider-compatible sentinel, not a real key:

```bash
(cd "$WORKSPACE" && uv lock)
sbx rm "$SANDBOX"
sbx secret ls --sandbox "$SANDBOX"
# Refuse an existing Anthropic secret unless it belongs to this run.
read -r -s -p 'Anthropic API key: ' ANTHROPIC_API_KEY; echo
printf '%s' "$ANTHROPIC_API_KEY" | sbx secret set-custom \
  --sandbox "$SANDBOX" --host api.anthropic.com \
  --env ANTHROPIC_PROXY_KEY --placeholder sk-ant-proxy-managed
unset ANTHROPIC_API_KEY
sbx create --name "$SANDBOX" "$WORKLOAD" "$WORKSPACE"
sbx policy allow network --sandbox "$SANDBOX" api.anthropic.com
# Reapply any custom Band deployment host grant before starting.
tmux new-session -s "$SANDBOX" "sbx run --name '$SANDBOX'"
```

Watch the owned pane for successful locked dependency sync and startup. In the
Band room mention the agent with `Explain what a Docker sandbox does in one
sentence.` Verify a completed turn and a model-generated answer rather than
an `echo:` response. Inspect `sbx policy log "$SANDBOX"` if startup or provider
calls fail; do not widen the policy to all hosts.

Cleanup the provider secret independently, in addition to the quickstart's
sandbox, Band secret and owned UI resources:

```bash
sbx secret rm --placeholder sk-ant-proxy-managed --sandbox "$SANDBOX" -f
sbx secret ls --sandbox "$SANDBOX"
```

Workspace and dependency edits require a launcher restart; changes to the
workload or injected secret environment require removal and recreation.

## Start from a repository

The launcher can clone a project at startup. Keep `band.yaml` at the workspace
root, set `project.path` to a subdirectory such as `app`, and uncomment its
annotated HTTPS `repo` block. The clone must contain `main.py`,
`pyproject.toml` and committed `uv.lock`. Grant the repository host before
launch. On restart, the existing checkout is validated and reused.
