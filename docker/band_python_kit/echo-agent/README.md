# Echo-agent starter workspace

A complete `uv` workspace for the Band Python workload. This guide includes
host setup, scoped credentials, creation, one owned PTY, a visible echo reply
and cleanup. The echo agent needs no LLM key or paid inference.

## Published echo quickstart

Use a supported Docker Sandboxes host with Docker, `sbx` 0.47.0, `curl`, `tar`
and `tmux`. On macOS, install `sbx` with `brew install docker/tap/sbx` and tmux
with `brew install tmux`. Run `sbx version` and `sbx login` before continuing. Inspect `sbx policy ls`:
if it reports that the global policy is uninitialized, run
`sbx policy init balanced` once. Preserve an existing policy; do not reset or
replace it. Organization policy may require administrator access.
Choose a **released v3 version** from the release notes; existing immutable v2
tags remain v2 and cannot be used with these instructions. The first v3 release
holds floating tags until acceptance, so use its concrete version.

This route requires a pre-registered Band **agent** id and agent API key. It
requires no LLM key or paid inference. An entitled **user** API key is only
needed for the optional self-registration route below.

Define your version, unique sandbox name and workspace once in a Bash terminal:

```bash
read -r -p 'Released v3 SDK version (X.Y.Z): ' VERSION
SANDBOX=my-band-echo
WORKSPACE="$HOME/my-band-echo"
WORKLOAD="docker.io/bandhq/band-python-kit:$VERSION"
BAND_SECRET_HOST='**.band.ai'
mkdir "$WORKSPACE"
curl -fsSL "https://codeload.github.com/band-ai/band-sdk-python/tar.gz/refs/tags/band-sdk-v$VERSION" \
  | tar -xz --strip-components=4 -C "$WORKSPACE" \
      "band-sdk-python-band-sdk-v$VERSION/docker/band_python_kit/echo-agent"
```

Edit `$WORKSPACE/band.yaml`: replace `agent.id` with your registered agent id.
The supplied production endpoints are `https://app.band.ai` and
`wss://app.band.ai/api/v1/socket/websocket`. For another deployment, edit both
endpoints and use its exact hostname as `BAND_SECRET_HOST`.

### Credential custody

The host proxy keeps the real key and substitutes it in outbound headers; the
VM receives the nonsecret `proxy-managed` placeholder. Secrets must exist
**before creation** to inject their environment variable. Check the selected
name first: if it has any secrets or an existing sandbox you do not own, choose
a new name. `set-custom` can overwrite an existing secret; do not use it to
adopt someone else's resources.

```bash
sbx ls
sbx secret ls --sandbox "$SANDBOX"
store_band_secret() {
  local band_key
  read -r -s -p 'Band agent API key: ' band_key || return
  echo
  printf '%s' "$band_key" | sbx secret set-custom \
    --sandbox "$SANDBOX" --host "$BAND_SECRET_HOST" \
    --env BAND_API_KEY --placeholder proxy-managed
}
store_band_secret
sbx create --name "$SANDBOX" "$WORKLOAD" "$WORKSPACE"
```

The real key is passed on stdin, never on command arguments or in the mounted
workspace. Keep shell tracing disabled when reading or transferring credentials.
Keep the same Bash terminal for subsequent commands; it owns these variables
and the `store_band_secret` function. The key is local to the function and is
not retained in the shell after storage.
The baseline allows Band Cloud and PyPI. For a different deployment, grant its
host before launching:

```bash
# Only for a deployment outside the baseline Band Cloud hosts:
# sbx policy allow network --sandbox "$SANDBOX" your-band-host.example
```

Start exactly one foreground session. A host tmux session owns its PTY:

```bash
tmux new-session -s "$SANDBOX" "sbx run --name '$SANDBOX'"
```

In the Band UI, create a room (or select a room you own), add the registered
agent, and send `hello sandbox` mentioning it. Expect `echo: hello sandbox`
and a completed turn. The attached pane contains launcher output and any
startup error. A running sandbox alone does not prove an agent replied.

### Observe, restart and clean up

Detach tmux with **Ctrl-b d**; that retains the owned PTY. Reattach from another
terminal with `tmux attach -t "$SANDBOX"` (define the same variables there). Exiting or killing the pane ends
its `sbx run` owner. Do not start a second `sbx run` just to view output.

```bash
sbx ls
tmux capture-pane -p -t "$SANDBOX" -S -100
sbx policy log "$SANDBOX"
```

Workspace code and lock changes are read at the next launcher start. For a
restart, stop the sandbox, end the old tmux owner if it remains, then create
one new owner. Do not assume `sbx stop` delivers SIGTERM or graceful teardown.

```bash
sbx stop "$SANDBOX"
if tmux has-session -t "$SANDBOX" 2>/dev/null; then
  tmux kill-session -t "$SANDBOX"
fi
tmux new-session -s "$SANDBOX" "sbx run --name '$SANDBOX'"
```

Send a fresh message and verify another completed echo. Workload changes
require removal and recreation; workspace/dependency changes only need the
restart above. **Removal deletes sandbox-scoped secrets.** Before removing a
sandbox, make sure you can supply the same agent key again from your own secure
credential store. Stop and close the old PTY, remove the sandbox, then call
`store_band_secret` and restore any provider secrets before repeating create
and the per-sandbox network grants with the selected workload. Finally start
one new tmux owner. A stop/start alone retains scoped secrets.

For final cleanup, attempt each owned resource independently, even if an earlier
command fails. Record failures and retry them; do not claim cleanup succeeded
until `sbx ls` and scoped secret listing confirm absence:

```bash
sbx stop "$SANDBOX"
if tmux has-session -t "$SANDBOX" 2>/dev/null; then
  tmux kill-session -t "$SANDBOX"
fi
sbx rm "$SANDBOX"
sbx secret rm --placeholder proxy-managed --sandbox "$SANDBOX" -f
sbx ls
sbx secret ls --sandbox "$SANDBOX"
```

Removing the sandbox does not remove Band resources. In the Band UI, delete
only the room and agent you created for this run; retain pre-existing identities.
After interruption, inspect the sandbox, scoped secrets, tmux owner and UI
resources by the recorded name/id before resuming or cleaning up. Keep those
identifiers until all cleanup is confirmed.

### Self-registration (no pre-provisioned agent)

Use the same starter and variables above, but leave `agent.id` as its placeholder
and skip the manual agent-secret/create steps. Install the **matching** host SDK
with `uv tool install "band-sdk==$VERSION"`; install host `uv` first using
[its installation guide](https://docs.astral.sh/uv/getting-started/installation/). Supply an entitled Band user key:

```bash
read -r -s -p 'Entitled Band user API key: ' BAND_API_KEY_USER; echo
export BAND_API_KEY_USER
band-kit provision --name "$SANDBOX" --agent-name 'My sandbox echo' \
  --description 'Echo agent in a Docker Sandbox' --workspace "$WORKSPACE" \
  --create --kit "$WORKLOAD" --host "$BAND_SECRET_HOST"
unset BAND_API_KEY_USER
tmux new-session -s "$SANDBOX" "sbx run --name '$SANDBOX'"
```

For another deployment, export the matching `BAND_REST_URL` and `BAND_WS_URL`
for host provisioning as well as editing `band.yaml`. The public Band `--kit`
option accepts the v3 workload reference. Provision writes the agent id,
stores its scoped secret through stdin and creates the sandbox; it does not
start the foreground agent. A repeated provision reuses matching identity and
secret and independently checks whether creation is needed. A mismatched
workspace/name is refused. `plan_required`, auth and provisioning failures
block this route; do not substitute an agent key for the user key.

Record the created id from `band.yaml` immediately for recovery. If registration
was interrupted before its id was returned, inspect the named agent in the
Band UI before retrying: its one-time key may be unrecoverable. Follow the
cleanup above, including the newly created Band agent and room.

Self-registration stores the one-time agent key only in sbx. If you do not
have that key independently, use stop/start for workspace edits. Destructive
recreation requires a newly registered identity: clean up the old owned Band
agent/room, restore the starter's placeholder id and provision with a fresh
name. Re-running provision with a saved agent id and a missing secret fails
closed; it cannot recover the old key.

## Files and identity

| File | Purpose |
|---|---|
| `band.yaml` | Schema 1 identity, endpoints, credential mode and runtime paths |
| `main.py` | Echo adapter and shutdown handling for delivered signals |
| `pyproject.toml`, `uv.lock` | Customer dependencies; the committed lock is required |
| `secrets.env.example` | Optional plaintext credential template |
| `.gitignore` | Keeps `.band/` and virtualenvs out of Git |

Set `agent.id` in `band.yaml` to a registered agent id. The optional
[host provisioning route](#self-registration-no-pre-provisioned-agent)
can register an agent and fill this field using an entitled user key.
Unknown configuration fields fail startup. The mounted workspace can be at
any host path; do not replace the sandbox-owned runtime paths with mount paths.

## Credentials

The default `credentials.source: proxy-managed` uses a host-side secret and a
nonsecret sentinel in the VM. Follow the canonical
[credential custody instructions](#credential-custody), including
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

This conversion starts from the pre-registered echo route in this guide and
uses the same Bash terminal, variables and `store_band_secret` function. Have
the same Band agent key available before removing the sandbox. For an identity
created only through self-registration, follow its identity-replacement
instructions above instead of deleting its only stored key.

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

Refresh the actual customer lock on the host. Removing the stopped sandbox
also deletes its scoped secrets. Restore the Band secret and store the provider
secret **before recreating**
so `ANTHROPIC_PROXY_KEY` is available at launch. sbx reserves
`ANTHROPIC_API_KEY`; the application reads the nonreserved variable and passes
its sentinel explicitly as the adapter's `provider_key`. The `sk-ant-` placeholder is a
nonsecret provider-compatible sentinel, not a real key:

```bash
(cd "$WORKSPACE" && uv lock)
sbx rm "$SANDBOX"
sbx secret ls --sandbox "$SANDBOX"
store_band_secret
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
