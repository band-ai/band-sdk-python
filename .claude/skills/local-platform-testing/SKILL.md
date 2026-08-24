---
name: local-platform-testing
description: Stand up thenvoi-platform locally (Docker infra + a headless Phoenix server) and point the SDK at it for a real, non-mocked integration test — no VPN, no shared dev/prod platform. Use when asked to test a feature "against a real/live/local platform", when ff_file_transfer or another on-prem-only feature flag needs to be exercised, or when troubleshooting a local platform that won't start, rejects an API key, or serves the wrong platform to the SDK.
---

# Local platform testing

Runs `thenvoi-platform` from source on this machine and drives it with the SDK
directly (`AgentTools` + the E2E baseline provisioning toolkit) — a genuine
end-to-end check with no LLM required, distinct from `tests/e2e/baseline`
(which targets the shared dev platform over VPN, see `.env.test`/CLAUDE.md's
Environment Variables section) and from unit tests (mocked REST client). Reach
for this when a fix needs to be proven against a real deployment — e.g. an
on-prem-only feature flag (`ff_file_transfer`) that never exists on SaaS.

Every step below exists because it silently fails a different way otherwise —
follow them in order rather than skipping to "just start the server." Assume
nothing about the machine: the platform repo may not be cloned, may be stale,
its toolchain/dependencies may never have been installed, and neither its
location nor its per-checkout config (`ADMIN_FUSIONAUTH_ID`, etc.) can be
guessed — every such value gets resolved explicitly below, and the human is
asked whenever it can't be. Run this skill from the SDK repo root and capture
that before moving anywhere else:

```bash
SDK_DIR="$(pwd)"
```

## Step 0 — Prerequisites and fresh checkout

Check each of these; don't assume any is already satisfied.

```bash
docker info >/dev/null 2>&1 || echo "Docker is not running -- start it first"
```

- **Locate the platform repo — never guess by directory name, never silently
  clone.** A checkout can be named anything (`thenvoi-platform`,
  `band-platform`, a fork, whatever the user called it) — identify it by
  content, not name: it's the repo whose `mix.exs` declares `app:
  :thenvoi_com`. Only `$THENVOI_PLATFORM_DIR` (if the user already set it) or
  a same-named sibling of this SDK checkout that passes that content check
  count as found automatically; anything else means asking:
  ```bash
  is_platform_repo() { [ -f "$1/mix.exs" ] && grep -q "app: :thenvoi_com" "$1/mix.exs"; }

  PLATFORM_DIR="${THENVOI_PLATFORM_DIR:-}"
  [ -n "$PLATFORM_DIR" ] && ! is_platform_repo "$PLATFORM_DIR" && PLATFORM_DIR=""
  [ -z "$PLATFORM_DIR" ] && is_platform_repo "$SDK_DIR/../thenvoi-platform" \
    && PLATFORM_DIR="$(cd "$SDK_DIR/../thenvoi-platform" && pwd)"
  echo "PLATFORM_DIR=${PLATFORM_DIR:-<not found>}"
  ```
  If that came back `<not found>`, **stop and ask the user** where their
  `thenvoi-platform` (or equivalently-named fork) checkout is, or whether to
  clone a fresh one and where — do not guess a path or clone unprompted.
  Once they answer, set `PLATFORM_DIR` accordingly, cloning only if they
  confirm there isn't one yet:
  ```bash
  git clone --recurse-submodules https://github.com/thenvoi/thenvoi-platform.git "$PLATFORM_DIR"
  ```
- **Repo present but possibly stale?** Don't blindly pull over uncommitted
  work:
  ```bash
  cd "$PLATFORM_DIR"
  git status --short          # stop and ask if this is non-empty and not yours
  git fetch origin main
  git checkout main && git pull --ff-only origin main
  ```
- **Elixir/Erlang/Node toolchain.** Don't hand-roll a `mise`/`asdf` check —
  the platform's own `make check-version-manager-prereqs` already detects
  which one is present, installs missing plugins/versions from
  `.tool-versions`, and prints install instructions (plus opens the docs) if
  neither exists:
  ```bash
  cd "$PLATFORM_DIR"
  make check-version-manager-prereqs
  ```
  Every later command that invokes `mix` is prefixed `mise exec --` (or
  `asdf exec --`, or dropped if the versions are already the active shell's
  default) so it resolves against `.tool-versions`, not a system-wide
  install.
- **`ADMIN_FUSIONAUTH_ID` — required, and not something to default without
  asking.** It must be the UUID of an admin FusionAuth user; a fresh `.env`
  has no value for it at all, and `make generate-secrets` below refuses to
  proceed without one. Check what's already there before assuming anything:
  ```bash
  [ -f "$PLATFORM_DIR/.env" ] || touch "$PLATFORM_DIR/.env"
  ADMIN_FUSIONAUTH_ID="$(grep -oP '(?<=^ADMIN_FUSIONAUTH_ID=).+' "$PLATFORM_DIR/.env" 2>/dev/null)"
  echo "ADMIN_FUSIONAUTH_ID=${ADMIN_FUSIONAUTH_ID:-<not set>}"
  ```
  If that's `<not set>`, **ask the user** whether to use the seeded
  kickstart admin (`00000000-0000-0000-0000-000000000001` — matches
  `development_analytics_tools/fusionauth/kickstart/kickstart.json`, the
  right choice for a from-scratch local dev database) or a UUID of their
  own. Once they answer, write it in and keep the shell variable in sync —
  every later step reuses `$ADMIN_FUSIONAUTH_ID`, never a hardcoded literal:
  ```bash
  echo "ADMIN_FUSIONAUTH_ID=$ADMIN_FUSIONAUTH_ID" >> "$PLATFORM_DIR/.env"
  ```
- **`OBAN_PRO_KEY`** is a private Hex repo credential (Oban Pro) that cannot
  be generated or guessed. It's validated automatically in Step 2 below
  (`make generate-secrets` depends on `check-oban-repo`) — if that step
  fails on it, stop and get one from whoever owns the platform's Oban Pro
  access; there is no workaround.

## Step 1 — Bring up Docker infra

Don't hand-roll a combined `docker compose up -d <all services>` — a slow or
failing service can leave another silently absent while the rest come up fine,
with nothing waiting on it. The platform's own `make ensure-docker-services`
target already starts `db` first, waits for it to report healthy, then brings
up each remaining service independently (so one failure doesn't block the
rest) and creates the file-transfer bucket — use it instead of reimplementing
that sequencing:

```bash
cd "$PLATFORM_DIR"
make ensure-docker-services
```

`docker compose ps` should show `db`, `unleash`, `minio` healthy before continuing.

## Step 2 — Generate secrets and install dependencies

Reuse the platform's own targets rather than re-deriving what they already
check — `generate-secrets` fills in `SECRET_KEY_BASE`/`CLOAK_KEY`/etc. and
validates `ADMIN_FUSIONAUTH_ID`/`OBAN_PRO_KEY` (failing loud, with actionable
messages, if either is missing); `setup` installs `mix`/npm dependencies and
runs `ecto.setup` — which needs the `db` container from Step 1 already up:

```bash
cd "$PLATFORM_DIR"
mise exec -- make generate-secrets
mise exec -- make setup
```

## Step 3 — Start the Phoenix server headless

**Do not use `make dev`.** It runs `iex -S mix phx.server`; with no TTY attached
(any backgrounded/non-interactive invocation), IEx hits EOF on stdin and exits
immediately — taking the whole BEAM node, Phoenix included, down with no error
message. Use the plain server command instead, and make sure it actually keeps
running (a bare `command &` inside one shell call gets reaped once that call's
process group ends — use your tool's real background-job tracking, e.g. Claude
Code's `run_in_background: true`, not a shell `&`):

```bash
cd "$PLATFORM_DIR"
mise exec -- mix phx.server < /dev/null   # mise supplies elixir/erlang from .tool-versions
```

Wait for `curl -sf http://localhost:4000/health` to return before continuing.

## Step 4 — Enable the feature flag under test (if it's on-prem-only)

`<FLAG_NAME>` below is whatever flag the task at hand needs (e.g.
`ff_file_transfer`) — take it from the task/user, never guess which flag to
flip. Flags like it stay off by default even in local dev — the Unleash seed
(`thenvoi-basic-flags.json`) is deliberately `enabled: false` with its
strategy `disabled: true`, and re-imports on every Unleash restart so a
developer's choice is never silently overridden. Flip it via the admin API
(local default creds, `AUTH_TYPE=open-source` still requires login):

```bash
curl -s -c /tmp/unleash_cookies.txt -X POST http://localhost:4242/auth/simple/login \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"unleash4all"}'

# get the strategy id for the flag
curl -s -b /tmp/unleash_cookies.txt \
  http://localhost:4242/api/admin/projects/default/features/<FLAG_NAME> | python3 -m json.tool

# both of these are required -- env-enabled alone does nothing if the strategy is disabled
curl -s -b /tmp/unleash_cookies.txt -X PUT \
  "http://localhost:4242/api/admin/projects/default/features/<FLAG_NAME>/environments/development/strategies/<STRATEGY_ID>" \
  -H "Content-Type: application/json" -d '{"name":"default","constraints":[],"variants":[],"parameters":{},"segments":[],"disabled":false}'
curl -s -b /tmp/unleash_cookies.txt -X POST \
  "http://localhost:4242/api/admin/projects/default/features/<FLAG_NAME>/environments/development/on"
```

The running server doesn't see this instantly — its Unleash client polls for
flag changes every 15 seconds (`features_period` in `config/config.exs`), not
on every request. Wait past that interval before relying on the flag in
Step 5/6, or a request made in the gap sees the fail-closed default:

```bash
sleep 20
```

## Step 5 — Get a working `BAND_API_KEY_USER`

A FusionAuth kickstart user has no local platform `users` row until it first
logs in for real — that row is created lazily, keyed by `fusionauth_uuid`. An
API key minted against `$ADMIN_FUSIONAUTH_ID` fails with `"API key not linked
to a user or agent"` (401) until the row exists. Create it once, then mint
the key — both `mix run -e` calls read `ADMIN_FUSIONAUTH_ID` from the
environment rather than a hardcoded literal, so export the value resolved in
Step 0 first:

```bash
cd "$PLATFORM_DIR"
export ADMIN_FUSIONAUTH_ID

mise exec -- mix run -e '
uuid = System.fetch_env!("ADMIN_FUSIONAUTH_ID")
case ThenvoiCom.Accounts.register_fusionauth_user(%{fusionauth_uuid: uuid, email: "admin@band.ai", first_name: "FusionAuth", last_name: "Admin"}) do
  {:ok, user} -> IO.puts("created user id=#{user.id} role=#{user.role}")
  {:error, cs} -> IO.inspect(cs.errors, label: "FAILED")
end'

mise exec -- mix run -e '
uuid = System.fetch_env!("ADMIN_FUSIONAUTH_ID")
name = "local-test-#{System.system_time(:second)}"
{:ok, _key, plain} = ThenvoiCom.Context.ApiKeys.create_api_key_with_value(%{name: name, fusionauth_uuid: uuid})
File.write!("/tmp/band_user_api_key.txt", plain)
IO.puts("wrote key name=#{name}, length=#{String.length(plain)}")'
```

`ApiKey` has a `unique_index` on `(fusionauth_uuid, name)` — a fixed name like
`"local-test"` mints fine the first time, then fails with a changeset error on
every later run against the same persistent local database. The timestamped
name above sidesteps that without needing to look up or delete the prior key.

Both `mix run -e` invocations boot a **second** BEAM node against the same
Postgres. Run them only while the Step 3 server is stopped (they'll collide on
the fixed PromEx port `9568`), then restart the server.

## Step 6 — Point the SDK at it and write the test

From the SDK repo root, reuse `tests/e2e/baseline/toolkit/provisioning.py`
(`ResourceManager`, `agent_rest_client`, `user_rest_client`) to register agents
and rooms, then call `band.runtime.tools.AgentTools` methods directly — no LLM
needed for a platform-integration check. Set **both** aliases the settings
class checks, or the dev platform wins silently:

```bash
cd "$SDK_DIR"
PYTHONPATH=. \
BAND_BASE_URL=http://localhost:4000 BAND_REST_URL=http://localhost:4000 \
BAND_API_KEY_USER=$(cat /tmp/band_user_api_key.txt) \
uv run python your_manual_test.py
```

`tests/e2e/baseline/settings.py`'s `rest_url` field declares
`AliasChoices("BAND_BASE_URL", "BAND_REST_URL")` — pydantic-settings picks the
**first alias present in the environment**, not "the one you just set."
`.env.test` is loaded with `override=False`, so if only `BAND_REST_URL` is
exported, `.env.test`'s `BAND_BASE_URL` (the shared dev platform) still wins
and the test silently runs against the wrong deployment.

## Guardrails

- **An agent's own message list is `direct_only`.** `GET
  /api/v1/agent/{chat_id}/messages` (`list_agent_messages`, which
  `list_room_files`/`read_room_file`/`_find_attachment` all read from) only
  returns messages that `@mention` that agent — never ones it authored. An
  uploader cannot see its own `send_room_file` result via its own
  `list_room_files`. Verify a sender's own action room-wide via the human/user
  client (`list_my_chat_messages`, not mention-scoped) or from the mentioned
  recipient's own tools instead.
- **Never commit or print the generated API key.** Write it to a scratch path
  outside the repo, same as any other credential.
- **Tear down deliberately, not by default.** Killing the Phoenix server
  (`lsof -ti:4000,9568 | xargs kill`) is cheap and safe to redo. Leaving
  `ff_file_transfer` on and the test user/key in place is usually fine (it's a
  local dev database), but confirm with whoever owns the checkout before
  reverting flag state someone else might be relying on.
