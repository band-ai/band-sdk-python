# Enterprise Teams qualification

This standalone harness serves multiple employees in one Microsoft 365 tenant.
Each employee links a separate hosted Band account and uses a private one-human
team, standard General channel in Posts layout. Two supplied Band rooms per
person are the qualification fixtures. Consumer accounts and guests are excluded.

**Status: live qualification BLOCKED.** Enterprise accounts, registration rights,
and hosting have not been supplied. Offline tests do not establish a GO.
The complete enterprise bridge requires a recorded qualification GO first.

## What runs where

The customer operates one container/process behind public HTTPS. Teams hosts the
app manifest and bot connection; Band hosts its existing APIs. The customer owns
Microsoft identities, tenant consent, app secrets, ingress and local persistent
disk. No Azure subscription is required by the default Teams-managed registration
path. The bridge creates no Microsoft accounts or teams.

Band tokens and reply bodies remain in memory. SQLite on the mounted local disk
stores correlation, claims and outcomes only. A separate SQLite exclusive lock
rejects a second process on the same state directory. Restart requires fresh
sign-in and destination confirmation. Unconfirmed work is cancelled; interrupted
writes become unknown and are never replayed automatically. Do not use shared
network storage or multiple replicas.

## Exact operator setup

1. A Microsoft 365 developer sandbox is suitable if eligible: its E5 subscription
   includes Teams and 25 user licenses. Check **Set up E5 subscription** at
   <https://developer.microsoft.com/en-us/microsoft-365/profile>. Eligibility is
   required; see <https://learn.microsoft.com/en-us/office/developer-program/microsoft-365-developer-program-get-started>.
   Alternatively, obtain a Teams-enabled Microsoft 365 tenant and two employee work/school
   accounts. IT must permit custom app installation and team-scoped
   `ChannelMessage.Read.Group` resource-specific consent. Confirm the chosen
   Microsoft subscription includes the needed Teams capabilities.
2. For each employee, create a **private team containing only that employee**
   and use its standard General channel in Posts layout. Record the employee's
   Entra object ID, tenant ID, team ID, group ID and channel ID. This is operator
   configuration; the runtime checks every roster page before forwarding/sending.
3. Prepare two hosted Band accounts with independent evidence of human API and
   user-WebSocket admission. Supply two synthetic fixture rooms per person and
   at least two eligible non-self recipients for target-change trials. Sharing
   test Band rooms is permitted; Teams destinations must remain separate.
4. Retrieve the documented public Band OAuth client ID from the platform owner.
   Set `BAND_OAUTH_CLIENT_ID` in the repository-root `.env.test`. Keep the hosted
   Band REST/WS defaults. An API key is never a substitute for device OAuth.
5. Use Node >=22.12 and `npx --yes @microsoft/teams.cli@3.0.4 --help`, then the
   registration subcommand's `--help`. Register with `--teams-managed` and explicit
   `--sign-in-audience myOrg`:

   ```sh
   npx --yes @microsoft/teams.cli@3.0.4 app create --name "Band qualification" --teams-managed --sign-in-audience myOrg --endpoint "https://YOUR-APPROVED-HOST/api/messages" --env band-teams/operator/teams.env
   ```

   Run this once after checking the output path does not exist; it creates remote
   resources and a credential file. Record the redacted registration, CLI version,
   organization audience and endpoint. CLI details:
   <https://microsoft.github.io/teams-sdk/cli/concepts/bot-locations/>.
6. Set personal/team scopes and application RSC `ChannelMessage.Read.Group` in
   the app manifest. Download and inspect the installed app package. Install it
   personally for both employees and into their teams; obtain owner consent.
   Keep its extracted manifest as `operator/installed-manifest.json`.
7. Create `band-teams/operator/` privately. Copy `.env.example` to
   `operator/teams.env` and set the app ID, secret and tenant. Never overwrite
   existing credentials blindly. Create `operator/run.json` using the example
   below. Put redacted independent admission evidence at `operator/admission.json`.
8. For the first environment, obtain an approved HTTPS tunnel forwarding to
   `127.0.0.1:3978`. Set the registration endpoint to its `/api/messages` URL.
   Record expiry in the manifest. Build/start the container below. Authentication
   is always enabled; there is no unsigned development mode.
9. Repeat on a customer-operated server/VM behind stable HTTPS with local disk,
   using the **same image digest**. Set deployment to `stable-server`, remove
   tunnel expiry, update the endpoint, and record this environment separately.
   Stop the local instance before switching; never share the live bot endpoint
   or state volume between concurrently running instances.

`run.json` (replace every placeholder; paths are relative to this file):

```json
{
  "deployment": "local-tunnel",
  "ingress_url": "https://YOUR-APPROVED-HOST/api/messages",
  "tunnel_expires_at": 1900000000,
  "installed_manifest": "installed-manifest.json",
  "entitlement_evidence": "admission.json",
  "teams_client_version": "RECORD-ACTUAL-DESKTOP-VERSION",
  "posts_layout_verified": true,
  "people": [
    {
      "alias": "alice", "tenant_id": "TENANT", "actor_id": "ALICE-ENTRA",
      "band_user_id": "ALICE-BAND", "team_id": "ALICE-TEAM",
      "channel_id": "ALICE-CHANNEL", "group_id": "ALICE-GROUP",
      "private_owner_verified": true, "rsc_consent_verified": true,
      "rooms": [{"id": "ROOM-ONE", "recipients": ["RECIPIENT-ONE", "RECIPIENT-TWO"]},
                {"id": "ROOM-TWO", "recipients": ["RECIPIENT-ONE"]}]
    },
    {
      "alias": "bob", "tenant_id": "TENANT", "actor_id": "BOB-ENTRA",
      "band_user_id": "BOB-BAND", "team_id": "BOB-TEAM",
      "channel_id": "BOB-CHANNEL", "group_id": "BOB-GROUP",
      "private_owner_verified": true, "rsc_consent_verified": true,
      "rooms": [{"id": "ROOM-ONE", "recipients": ["RECIPIENT-ONE", "RECIPIENT-TWO"]},
                {"id": "ROOM-TWO", "recipients": ["RECIPIENT-ONE"]}]
    }
  ]
}
```

From the repository root:

```sh
uv sync --project band-teams --locked --group dev
uv run --project band-teams --locked python -m bandteams check-dependencies
uv run --project band-teams --locked python -m bandteams preflight --manifest band-teams/operator/run.json --teams-credentials band-teams/operator/teams.env --band-env .env.test --state band-teams/state
docker build -f band-teams/Dockerfile -t band-teams:qualification band-teams
docker compose -f band-teams/compose.yaml run --rm bridge preflight --manifest /config/run.json --teams-credentials /run/secrets/teams.env --band-env /run/secrets/band.env --state /state
docker compose -f band-teams/compose.yaml up bridge
```

The Compose host port is loopback-only. On the stable server place a TLS reverse
proxy in front, forwarding `/api/messages` without transport retries for POSTs.
Mount secrets at runtime; the build context explicitly excludes operator files.
The named volume is initialized for container UID 10001. Retain it across restart.

## Employee walkthrough

In the bot's personal chat: `sign in`. Open the bare verification page and type
the code there. Never send the device code to another person. Complete hosted
sign-in as the manifest's expected Band identity. Then `confirm destination`.
Use `status PROPOSAL_ID` to query a persisted outcome after fresh authorization.
Use `status`; Ready requires current roster/membership checks and subscriptions.

Use `recipient ROOM_ID RECIPIENT_ID` in personal chat when a room has multiple
eligible targets. This chooses future reviews; it never modifies an existing
review or sends a message. Native replies in the supplied room thread create a
fixed review. Check the full body and recipient, then **Send to Band** or Cancel.
The invoke acknowledges processing before remote checks. Delivered means Band
accepted a message ID; it does not mean an agent answered. Plain text is the
qualified outgoing envelope; unsupported formatting/attachments are refused.

Use `refresh` once per user to exercise token rotation and fresh-header WebSocket
creation. It retires old reviews. `sign out` fences that person and awaits task
and socket cleanup. The other employee remains independent. `new code` cancels
the previous sign-in and starts a fresh device grant.

## Qualification evidence

All Q1–Q9 start **NOT RUN**. Record code revision, image digest, dependency/CLI/
Teams client versions, timestamp, environment, safe user aliases, expected and
observed outcome, verdict, and redacted artifact references. Run the full matrix
for local tunnel and stable server. Capture real root/reply/action IDs, mentions,
navigation, exact reviewed recipient/body, zero pre-confirm sends, duplicates,
foreign actor/card/thread refusal, receipt failure, ambiguous response, sign-out,
roster change, restart, and cleanup. Keep codes/tokens/body hashes out of evidence.

Copy `evidence-template.json` into the private operator directory and fill it
from the measured run. `python -m bandteams report --evidence evidence.json` evaluates operator-recorded
results. It requires all 18 environment/probe cases and artifact files plus
independent admission evidence for GO. Offline tests never populate live results.
A harness failure remains a defect/BLOCKED result. Only a measured external
incompatibility with evidence can yield NO-GO; identify dependent probes NOT RUN
and stopped parent work. A generated GO still requires review of its evidence.

Q1 registration; Q2 hosting/authentication; Q3 two-user Band sign-in and refresh;
Q4 two-room routing; Q5 explicit confirmation; Q6 isolation; Q7 delivery faults;
Q8 lifecycle/restart; Q9 operator reproduction and cleanup.

## Checks and cleanup

```sh
uv run --project band-teams --locked pytest -c band-teams/pyproject.toml --confcutdir=tests/teams tests/teams -q
uv run --project band-teams --locked pytest -c band-teams/pyproject.toml --confcutdir=tests/teams/contracts tests/teams/contracts -q
uv run --project band-teams --locked pyrefly check --config band-teams/pyproject.toml
uv run --project band-teams --locked ruff check band-teams/src tests/teams
uv run --project band-teams --locked ruff format --check band-teams/src tests/teams
```

Stop with Compose down, preserving the volume until evidence and unresolved writes
are reconciled. Inspect the SQLite `records` table for append-only `resource`
entries; delete only run-created posts using their recorded IDs and the current
authorized Teams client. Record cleanup outcomes. Never delete operator-supplied
users, teams or rooms. An ambiguous creation must be reconciled manually before
reset; never silently recreate it. No delete-all command is included.

Unqualified parent gates: full discovery/history-live handoff, automatic recovery,
complete token lifecycle, encrypted persisted credentials, rich content, full
agent-answer loop, load limits, backup/restore and company operational acceptance.
