---
name: local-platform-testing
description: Stand up ~/repo/thenvoi-platform locally (Docker infra + a headless Phoenix server) and point the SDK at it for a real, non-mocked integration test — no VPN, no shared dev/prod platform. Use when asked to test a feature "against a real/live/local platform", when ff_file_transfer or another on-prem-only feature flag needs to be exercised, or when troubleshooting a local platform that won't start, rejects an API key, or serves the wrong platform to the SDK.
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
and its toolchain/dependencies may never have been installed.

## Step 0 — Prerequisites and fresh checkout

Check each of these; don't assume any is already satisfied.

```bash
docker info >/dev/null 2>&1 || echo "Docker is not running -- start it first"
command -v mise >/dev/null 2>&1 && echo "mise found" || echo "no mise -- see fallback below"
```

- **Docker** must be running (the infra in Step 1 is entirely containers).
- **Repo present?** If `~/repo/thenvoi-platform` doesn't exist:
  ```bash
  git clone --recurse-submodules https://github.com/thenvoi/thenvoi-platform.git ~/repo/thenvoi-platform
  ```
- **Repo present but possibly stale?** Don't blindly pull over uncommitted
  work:
  ```bash
  cd ~/repo/thenvoi-platform
  git status --short          # stop and ask if this is non-empty and not yours
  git fetch origin main
  git checkout main && git pull --ff-only origin main
  git submodule update --init --recursive
  ```
- **Elixir/Erlang/Node toolchain.** The repo pins versions in `.tool-versions`
  (`mise`- or `asdf`-compatible). If neither `mise` nor `asdf` is installed,
  stop and follow the README's Prerequisites section (asdf + elixir/erlang
  plugins) — installing a version manager isn't something to script blind.
  Otherwise:
  ```bash
  mise install        # or: asdf install
  ```
  Every command below uses `mise exec --`; substitute `asdf exec --` (or just
  drop the prefix if the versions are already the active shell's default) if
  using asdf instead.
- **`.env` file and its two non-generatable secrets.** `docker-compose.yml`
  declares `env_file: [.env.example, .env]` for several services and treats a
  *missing* `.env` as a hard error, so it must exist (empty is fine to start):
  ```bash
  [ -f .env ] || touch .env
  ```
  `ADMIN_FUSIONAUTH_ID` (`Makefile`'s `generate-secrets` will refuse to
  proceed without it) defaults to `00000000-0000-0000-0000-000000000001`
  (the seeded kickstart admin) if unset — that default is what the rest of
  this skill assumes. **`OBAN_PRO_KEY`** is a private Hex repo credential
  (Oban Pro) that cannot be generated or guessed — if it isn't already
  configured (`make check-oban-repo` from the platform repo reports its
  status), stop and get one from whoever owns the platform's Oban Pro
  access; `mix deps.get` fails without it.
- **Dependencies installed?** Safe to (re-)run even when already satisfied:
  ```bash
  cd ~/repo/thenvoi-platform
  mise exec -- mix deps.get
  mise exec -- mix setup       # ecto.create/migrate + asset deps; idempotent
  ```

## Step 1 — Bring up Docker infra

```bash
cd ~/repo/thenvoi-platform
docker compose up -d db unleash opensearch fusionauth otel-collector minio
docker compose up --exit-code-from minio-init minio-init      # creates the file-transfer bucket
```

`docker compose ps` should show `db`, `unleash`, `minio` healthy before continuing.

## Step 2 — Start the Phoenix server headless

**Do not use `make dev`.** It runs `iex -S mix phx.server`; with no TTY attached
(any backgrounded/non-interactive invocation), IEx hits EOF on stdin and exits
immediately — taking the whole BEAM node, Phoenix included, down with no error
message. Use the plain server command instead, and make sure it actually keeps
running (a bare `command &` inside one shell call gets reaped once that call's
process group ends — use your tool's real background-job tracking, e.g. Claude
Code's `run_in_background: true`, not a shell `&`):

```bash
cd ~/repo/thenvoi-platform
mise exec -- mix phx.server < /dev/null   # mise supplies elixir/erlang from .tool-versions
```

Wait for `curl -sf http://localhost:4000/health` to return before continuing.

## Step 3 — Enable the feature flag under test (if it's on-prem-only)

`ff_file_transfer` (and any flag like it) stays off by default even in local
dev — the Unleash seed (`thenvoi-basic-flags.json`) is deliberately `enabled:
false` with its strategy `disabled: true`, and re-imports on every Unleash
restart so a developer's choice is never silently overridden. Flip it via the
admin API (local default creds, `AUTH_TYPE=open-source` still requires login):

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

## Step 4 — Get a working `BAND_API_KEY_USER`

A FusionAuth kickstart user has no local platform `users` row until it first
logs in for real — that row is created lazily, keyed by `fusionauth_uuid`. An
API key minted against `ADMIN_FUSIONAUTH_ID` (`.env`'s default,
`00000000-0000-0000-0000-000000000001`) fails with `"API key not linked to a
user or agent"` (401) until the row exists. Create it once, then mint the key:

```bash
cd ~/repo/thenvoi-platform
mise exec -- mix run -e '
case ThenvoiCom.Accounts.register_fusionauth_user(%{fusionauth_uuid: "00000000-0000-0000-0000-000000000001", email: "admin@band.ai", first_name: "FusionAuth", last_name: "Admin"}) do
  {:ok, user} -> IO.puts("created user id=#{user.id} role=#{user.role}")
  {:error, cs} -> IO.inspect(cs.errors, label: "FAILED")
end'

mise exec -- mix run -e '
{:ok, _key, plain} = ThenvoiCom.Context.ApiKeys.create_api_key_with_value(%{name: "local-test", fusionauth_uuid: "00000000-0000-0000-0000-000000000001"})
File.write!("/tmp/band_user_api_key.txt", plain)
IO.puts("wrote key, length=#{String.length(plain)}")'
```

Both `mix run -e` invocations boot a **second** BEAM node against the same
Postgres. Run them only while the Step 2 server is stopped (they'll collide on
the fixed PromEx port `9568`), then restart the server.

## Step 5 — Point the SDK at it and write the test

From `~/repo/thenvoi-sdk-python`, reuse `tests/e2e/baseline/toolkit/provisioning.py`
(`ResourceManager`, `agent_rest_client`, `user_rest_client`) to register agents
and rooms, then call `band.runtime.tools.AgentTools` methods directly — no LLM
needed for a platform-integration check. Set **both** aliases the settings
class checks, or the dev platform wins silently:

```bash
cd ~/repo/thenvoi-sdk-python
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
