# Band Python workload for Docker Sandboxes

Run a Band Python agent in a Docker Sandbox with locked project dependencies,
an isolated SDK installation and default-deny network policy. The v3 kit is
one OCI workload image; there is no separate kit artifact or image reference.
The supported validation baseline is **sbx 0.47.0**. Customer `band.yaml`
continues to use schema version `1`.

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
read -r -s -p 'Band agent API key: ' BAND_API_KEY; echo
printf '%s' "$BAND_API_KEY" | sbx secret set-custom \
  --sandbox "$SANDBOX" --host "$BAND_SECRET_HOST" \
  --env BAND_API_KEY --placeholder proxy-managed
unset BAND_API_KEY
sbx create --name "$SANDBOX" "$WORKLOAD" "$WORKSPACE"
```

The real key is passed on stdin, never on command arguments or in the mounted
workspace. Keep shell tracing disabled when reading or transferring credentials.
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
restart above. When changing workload, preserve the owned scoped secret:
stop and close the old PTY, `sbx rm "$SANDBOX"`, then repeat the positional
create and tmux run commands with the selected workload.

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

## Workspace and launch contract

The [echo starter guide](echo-agent/README.md) describes configuration, locked
dependencies and an optional Anthropic conversion. `uv sync --locked` runs in
sandbox-owned storage outside the mount and the immutable SDK venv. Missing or
stale locks fail visibly; update with `uv lock` on the host, then restart.

The image declares `USER agent` (UID 1000) and directly runs the existing Band
launcher. Docker Sandboxes supplies system proxy CA trust. `SSL_CERT_FILE` and
`REQUESTS_CA_BUNDLE` select the complete system bundle for Python clients.
The launcher discovers the mounted workspace, validates configuration,
credentials and paths, optionally clones a repository, syncs locked dependencies,
then execs the customer entrypoint. Signals actually delivered to that process
reach customer code; sandbox stop is not a graceful-signal guarantee.

The agent remains a foreground workload. Keep one owned real PTY for its
lifetime. `sbx create` does not start it; backgrounding a piped `sbx run` is not
a replacement for an owned PTY. Launcher diagnostics are phase-labelled and
also use the configured runtime log directory. Never override sandbox-managed
proxy variables.

The opt-in `workspace-env-file` credential tier places plaintext keys in the
mount and VM. Its guardrails and warning are documented in the starter guide;
prefer proxy-managed credentials. Policy and agent instructions are defined
once in the workload descriptor. Provider and source-control hosts require
explicit per-sandbox grants, for example:

```bash
sbx policy allow network --sandbox "$SANDBOX" api.anthropic.com
sbx policy allow network --sandbox "$SANDBOX" github.com
```

## Develop the workload

From the repository root, follow the exact build and local validation commands
in the [self-registration demo](../../examples/sandbox/self-registration/README.md).
That repository tool wraps the built image with the canonical descriptor for
local sbx consumption and owns the foreground PTY. It requires an entitled
user key in repository-root `.env.test`:

```bash
uv sync --extra dev
docker buildx build -f docker/band_python_kit/spec.yaml \
  --target runtime --load -t band-python-kit:local .
docker save band-python-kit:local | sbx template load /dev/stdin
uv run examples/sandbox/self-registration/demo.py --kit band-python-kit:local
```

Do not pass a host-local Docker image tag directly to `sbx create`: it is not
a published workload reference. The demo's descriptor wrapper preserves v3
metadata and uses `band-workload.yaml`; a local `spec.yaml` path can select the
legacy decoder in this sbx baseline.

The canonical Dockerfile has a lean `runtime` stage, an optional `cli` stage
for the meeting demo, and a default final stage derived from runtime. Both
variants finish as agent and receive the same descriptor metadata. Use
`--target cli` for the CLI variant; normal publication stays base-only.
`--build-arg SDK_EXTRA=anthropic` can pre-bake one SDK extra; the customer's
locked environment remains separate.

| Path | Purpose |
|---|---|
| `/opt/band/venv/bin/python` (`BAND_SDK_PYTHON`) | Immutable SDK/launcher interpreter, outside PATH |
| `/opt/band/bin/uv` (`BAND_SDK_UV`) | Pinned locked-sync executable, outside PATH |
| `/home/agent` | UID 1000 home and sandbox-owned runtime storage |

For direct Docker diagnostics, override the launcher entrypoint explicitly:

```bash
docker run --rm --entrypoint /opt/band/venv/bin/python band-python-kit:local \
  -c 'import band; print(band.__version__)'
```

See [RELEASING.md](RELEASING.md) for dual-registry workload publishing,
immutable tags, first-release acceptance and rebuild eligibility. There is
currently no Band kit entry in add-band or sbx-kits-contrib; catalog integration
is outside this migration.
