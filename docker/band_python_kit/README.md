# Band Python workload for Docker Sandboxes

Run a Band Python agent in a Docker Sandbox with locked project dependencies,
an isolated SDK installation and default-deny network policy. The v3 kit is
one OCI workload image; there is no separate kit artifact or image reference.
The supported validation baseline is **sbx 0.47.0**. Customer `band.yaml`
continues to use schema version `1`.

For the complete customer setup, echo, restart, cleanup and optional Anthropic
conversion, follow the [starter guide](echo-agent/README.md#published-echo-quickstart).
The release starter includes that guide alongside its application and lockfile.

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
