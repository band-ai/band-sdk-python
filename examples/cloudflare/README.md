# Cloudflare-hosted Python agent

A user mentions an Anthropic-backed agent in a Band room. A persistent
`band-bridge` on your infrastructure forwards the event over authenticated HTTP.
The Worker routes by room ID to a Durable Object and its Python container. The
shared [receiver](../oneshot/README.md) uses the existing `OneShotInvoker` and
Anthropic adapter to fetch current history, call Band tools, and reply over REST.
Each room has its own container; the bridge keeps ordinary same-room ordering.

## Prerequisites

Use a complete SDK checkout, Python 3.12+, uv 0.13.4+, Node 22+, Docker,
a Workers Paid Cloudflare account with Containers access, and matching Band
agent credentials plus an Anthropic key. The Worker and runtime tests use
[Python Workers](https://developers.cloudflare.com/workers/languages/python/).
Cloudflare's deployment CLI still requires Node; the commands below pin
Wrangler 4.153.0. OAuth via `npx --yes wrangler@4.153.0 login` is sufficient
for local deployment. CI can use a scoped Cloudflare API token instead. The bridge needs persistent user infrastructure.

Workers Paid starts at $5/month **plus usage**. Container CPU, memory, disk and
egress, Durable Objects, Anthropic inference, and your bridge host can add cost.
See [container pricing](https://developers.cloudflare.com/containers/platform/pricing/).
Idle shutdown reduces running time; this is not a $5 spending cap.

## Configure and deploy

From this directory:

```sh
uv sync --locked
uv run --locked pywrangler sync
npx --yes wrangler@4.153.0 login
```

Set unique Worker and container names in `wrangler.jsonc`; update both the
container application's `name` and `exports.Room.container` together. Set the
public `BAND_AGENT_ID` and HTTPS `BAND_REST_URL` in `vars`. An optional
`ANTHROPIC_MODEL` var overrides the receiver's SDK default. Select your intended
Cloudflare account. Python dependencies are locked in
`uv.lock` for deployment tooling and `pylock.toml` for the Worker runtime.
`pywrangler sync` installs the runtime packages into gitignored `python_modules/`;
repeat it after changing Python dependencies. No TypeScript generation is needed.

Copy `.dev.vars.example` to gitignored `.dev.vars`, with mode 600. Supply only
`BAND_API_KEY` (the **agent's** key), `ANTHROPIC_API_KEY`, and a random
`BRIDGE_TOKEN`. Preserve any existing private config before reconciling it.
The three names in `secrets.required` drive deployment checks.
Missing/empty settings also fail closed at runtime before routing or booting.

```sh
npx --yes wrangler@4.153.0 dev
# In a separate terminal, test with the bridge below pointing to localhost:8787.
# Then stop local Wrangler and deploy with secrets present atomically:
npx --yes wrangler@4.153.0 deploy --secrets-file .dev.vars
```

Use public production endpoints for deployed verification. The shared dev
platform requires VPN and cannot be reached from deployed containers. Load
provisioning and trigger credentials from repo-root `.env.test`; authenticate
the user and agent against the same platform before deploying. A user key
creates a disposable agent; its returned agent key goes in Worker secrets and
the bridge. Never put the provisioning user's key in the container or Worker.

## Start the separately hosted bridge

In repo-root `.env.test`, set matching platform URLs and this configuration,
substituting the agent's identity/key and the same bridge token as the Worker:

```dotenv
BAND_REST_URL=https://app.band.ai
BAND_WS_URL=wss://app.band.ai/api/v1/socket/websocket
BAND_BRIDGE_AGENTS='[{"agent_id":"your-agent-id","api_key":"your-agent-key","messages_only":true,"target":{"type":"http","url":"https://your-worker.workers.dev/invocations","bearer_token":"your-bridge-token","timeout":300}}]'
```

For local Wrangler use `http://localhost:8787/invocations` as the target. From
the complete repository checkout:

```sh
uv run --isolated examples/oneshot/run-bridge.py
```

Invite the agent to a room and mention it: ask it to call `band_get_participants`
and send a reply. Verify the posted tool events, actual reply, processed trigger,
and a follow-up using current history. Repeat in a second room with distinct
context. A successful `/ping` is startup evidence, not conversation proof.

HTTP/AgentCore target options reject unknown fields, including misspelled bearer
options. An explicitly empty bearer token is invalid. `messages_only` defaults
to false; this example opts in to avoid non-message cold starts. Room subscription
management still happens. Keep the default for AgentCore: its receiver needs
cleanup events. Here a room container's bounded lifetime replaces immediate
adapter cleanup; do not copy that choice into general multi-room receivers.

## Credential and lifetime boundaries

Only `examples/oneshot/server.py` and its `server.py.lock` are allowed in the
image build context. The context-root `.dockerignore` excludes the launcher and
every other file. The pinned Wrangler passes Dockerfile contents through stdin,
so a `Dockerfile.dockerignore` sidecar does not protect this build. Dependencies
install from the receiver's script lock, which resolves its PEP 723 metadata,
including qualified `band-sdk==6.0.1`. After changing that metadata, refresh it
with `uv lock --script examples/oneshot/server.py`.

Raw provider keys stay in Worker secrets. Containers receive `proxy-managed`
placeholders. The bridge bearer is removed before forwarding to the container. The Worker intercepts HTTPS only for the configured Band host and
`api.anthropic.com`, replaces `x-api-key`, and handles redirects manually.
`enableInternet:false` blocks other destinations. The entrypoint combines the
system CA bundle with Cloudflare's interception CA and fails if that CA is
missing. The non-root UID is file ownership hygiene: durable-object-scheduled
Cloudflare containers retain root capabilities, so UID is not a privilege boundary.

The Worker accepts only authenticated `POST /invocations` for its configured
agent with a nonempty string room ID. There is no agent-ID fallback. The SDK
still validates delivery envelopes and owns claims, completion, history and drain.
The Worker shares cold-start work, waits for `/ping` after receiver startup,
bounds readiness by `READINESS_SECONDS`, and destroys failed new boots before
permitting another attempt. It never retries an invocation. Healthy containers
shut down `IDLE_MS` after the Durable Object becomes inactive; request activity
can extend their lifetime. Both constants live in `src/worker.py`.
The Worker uses Python Workers and Durable Objects; the agent itself runs in the ordinary Python
container, with no Pyodide restrictions on SDK dependencies.

Connected turns have a 300-second bridge budget. An HTTP disconnect can cancel
Worker work; `waitUntil` is not a durable background job mechanism. Do not infer
completion after disconnect, automatic retry, or exactly-once execution across
crashes. In Python Worker qualification, a connected turn with a controlled
130-second provider delay completed in about 138 seconds. An accepted turn
whose client disconnected remained processing after 240 seconds, while the
container instance list reported `inactive`. Treat interrupted turns as recovery work, not background jobs.

## Failed-start recovery

An HTTP 503 is logged by the bridge without automatic retry. Message A can stay
oldest pending; sending B to a now-healthy receiver can return successfully
without advancing either message. After fixing startup, restart the single
owning bridge. Its existing startup rehydration asks for the oldest pending
message and nudges the normal SDK drain. Verify pending work settles and send
a fresh follow-up to prove recovery. Existing room PLAY control is an optional
operator nudge where available. A healthy ping or another mention is insufficient.

## Rotate keys or image

1. Let active turns settle, then stop the owning bridge to pause ingress.
2. Update the private secrets/image and deploy with `--secrets-file .dev.vars`.
   Update the bridge's bearer in the same paused window if rotating it.
3. Use `npx --yes wrangler@4.153.0 containers list` to find the recorded application ID and
   `npx --yes wrangler@4.153.0 containers instances APP_ID` to observe old instances becoming
   inactive after the idle timeout. A deployment can preserve a warm container
   and its captured interceptor props/image.
4. Resume the bridge. Verify the **actually serving** Worker/DO version and
   intended image/credential generation through controlled evidence before
   declaring rotation complete. Inspect `x-band-worker-version`,
   `x-band-room-version`, and `x-band-container-image` on a controlled invocation.
   If old code is still serving, pause and repeat.

This maintenance procedure accepts downtime. The qualified Wrangler CLI has
no per-instance restart command; do not add a public administrative route or
assume deployment acknowledgment proves immediate replacement.

## Clean up

Record each created resource ID immediately in an append-only private ledger:
Band agent and rooms, Worker, namespace, container application, registry tags,
and local processes/containers. Preserve credentials privately before subsequent
operations can fail. Apply the same cleanup after failed startup.

Stop the bridge and owned tail/dev processes; let work settle and observe idle
termination where possible. Retire the namespace before deleting the Worker:
remove the Room class, `ROOM` binding, and container configuration; replace the
Room export with `"Room": {"type":"durable-object","state":"deleted"}`; deploy that retirement using the
same Worker name. Then:

```sh
npx --yes wrangler@4.153.0 delete
npx --yes wrangler@4.153.0 containers delete APP_ID
npx --yes wrangler@4.153.0 containers images delete IMAGE_TAG
```

Use the recorded application ID and exact owned image tags. Verify the Worker
returns 404, the namespace and application/instances are absent, registry tags
are gone, owned local processes/containers stopped, owned Band rooms absent, and
the deleted agent's key rejected. Preserve all pre-existing resources and shared
caches. Deleting only the Worker is insufficient.

## Validation

From the repository root, run the bridge/shared-receiver and OneShot tests:

```sh
uv run --all-packages pytest tests/bridge tests/oneshot_server tests/agentcore_container tests/runtime/oneshot -q
```

The Python runtime tests require Node 22+, uv 0.13.4+, and network access to
install Cloudflare's pinned tools. They run the shipped Worker in real workerd,
with only the external container/HTTP boundaries doubled. From the repository
root:

```sh
CLOUDFLARE_TESTS_ENABLED=true uv run --all-packages pytest tests/cloudflare -q --no-cov
```

They are opt-in, like the existing Docker and live tests, and add no live CI
suite. Also run the repository's applicable author gate. Qualify local and
deployed conversations, follow-ups, room isolation, replay, simultaneous cold
starts, invalid ingress, blocked egress,
startup recovery, idle stop/restart, rotation, a connected tool turn over 125
seconds, disconnect observations, and complete cleanup. Keep unrun checks explicit.
AgentCore image/startup regression checks do not qualify a live AWS deployment.

See the [container API](https://developers.cloudflare.com/containers/api/durable-object-container/)
and [Workers duration limits](https://developers.cloudflare.com/workers/platform/limits/#duration).
