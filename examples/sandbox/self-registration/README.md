# Self-registration validation demo

This repository development tool registers a fresh agent, creates a v3 workload
sandbox, owns one foreground PTY, checks a completed `echo:` reply and repeats
provisioning to verify that it returns the same agent id. It uses the baseline
E2E toolkit; it is not part of the customer starter or installed SDK.

Run on a supported Docker Sandboxes host with `sbx` 0.47.0 or newer, Docker,
Buildx and `uv`. Sign in with `sbx login`; initialize policy if needed with
`sbx policy init balanced`. An entitled Band user key is required. Put
`BAND_API_KEY_USER`, `BAND_REST_URL` and `BAND_WS_URL` in repository-root
`.env.test`. The script loads this file over inherited Band variables. An
entitlement/provisioning failure blocks validation and may also block remote
cleanup; it is not a successful smoke test.

From the repository root:

```bash
uv sync --extra dev
# Build the local v3 workload, including its descriptor metadata:
docker buildx build -f docker/band_python_kit/spec.yaml --target runtime \
  --load -t band-python-kit:local .
docker save band-python-kit:local | sbx template load /dev/stdin
uv run examples/sandbox/self-registration/demo.py --kit band-python-kit:local
```

After the first v3 SDK release has passed registry acceptance, choose its
immutable version once and run the same scenario against the published workload:

```bash
SDK_VERSION=<released-v3-SDK-version>
uv run examples/sandbox/self-registration/demo.py \
  --kit "docker.io/bandhq/band-python-kit:$SDK_VERSION"
```

Expect a `Run name: band-selfreg-demo-...` log, a reply containing `echo:`,
`Confirmed idempotent`, then confirmed cleanup. `sbx create` alone does not run
the agent; the script retains one attached `sbx run` PTY throughout the scenario.

## Cleanup and interrupted-run recovery

The script records the run name and each known agent/room id immediately in
`examples/sandbox/self-registration/.runs/<run-name>/`. Cleanup attempts local
sandbox/secrets, rooms and agents independently. A failed cleanup returns a
nonzero exit code, retains unresolved ids and prints this recovery command:

```bash
uv run examples/sandbox/self-registration/demo.py \
  --cleanup band-selfreg-demo-<printed-suffix>
```

Use it after interruption or a host crash, once credentials/access are restored.
It also looks up the exact run display name if provisioning was interrupted
before the agent id was returned. Keep the ledger until cleanup succeeds. For
older runs without a room ledger, inspect and remove their room manually in the
Band UI. Removing a sandbox deletes its scoped host secrets, but not Band
agents or rooms. Cleanup also removes secrets independently when creation or
sandbox removal failed.
Never remove a pre-existing agent or room merely because it appeared in a failed
validation run.
