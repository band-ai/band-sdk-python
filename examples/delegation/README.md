# Delegated Credentials — the Two-User Example (M2)

An agent that acts with the **asker's** credentials, not its owner's.

Bob owns and runs `01_crm_specialist.py`. When **Alice** (a different user)
@mentions it, the platform mints a server-side identity envelope on her
message — *who is asking* — and the agent's custom tool exchanges that
envelope for a short-lived provider token that acts **as Alice**: her
consent, her provider connection. Bob's credentials are never used for
Alice's request, and Alice's token never outlives the message that earned it.

---

## ⚠️ Precondition: the exchange is INERT on a stock deployment

> **Read this before filing a bug.** The token exchange has a server-side
> opt-in that ships **dark** in M2:
>
> - Every MCP connector has a `delegation_credential_mode` field that
>   defaults to **`owner`** — "this connector only ever uses the agent
>   owner's credentials."
> - The exchange only issues delegated tokens for connectors whose mode is
>   **`originator`** or **`any`**.
> - That field currently has **no API or UI surface** — it can only be set
>   by seeding the database directly (a platform-side operation).
>
> Until your deployment has a connector seeded to `originator`/`any` (and
> the org's delegation switch on), **every** exchange call answers
> `403 delegation_denied` — deliberately undifferentiated, and exactly what
> this example will show you. That is the endpoint working as specified,
> not a bug in your setup.

## Prerequisites

1. **Two platform users** — Bob (owns the agent) and Alice (will @mention it),
   in the **same org** (cross-org delegation is hard-blocked in M2).
2. **A remote agent** created by Bob, with its `agent_id`/`api_key` in
   `agent_config.yaml` under `crm_specialist` (see
   `agent_config.yaml.example`).
3. **An MCP connector** on the deployment whose id (a UUID) you export as
   `CRM_CONNECTOR_ID` — seeded per the precondition box above.
4. **Anthropic API key** — `ANTHROPIC_API_KEY` in the environment (the agent's
   reasoning model; unrelated to the delegated token).

## The two-user flow

```text
Terminal (Bob)                        Web app (Alice)
--------------                        ---------------
uv run examples/delegation/\
  01_crm_specialist.py
                                      1. Add the agent to a room
                                      2. "@crm-specialist look up ACME-042"
   <- message arrives with metadata.delegation
      (originator=alice, message_id, minted_at)
   tool calls ctx.credentials.token_for(CRM_CONNECTOR_ID)
   -> first run: ConsentMissing -> agent tells Alice to grant consent
                                      3. Alice grants the agent consent
                                         (Settings -> Agent consents, or the
                                         /api/v1/me consent API)
                                      4. Alice asks again
   -> ProviderNotConnected -> agent tells Alice to connect the provider
                                      5. Alice connects the provider
                                         (Settings -> Connected apps)
                                      6. Alice asks again
   -> 200: a short-lived token acting AS ALICE — the tool answers
```

Run it:

```bash
export CRM_CONNECTOR_ID=<mcp-connector-uuid>   # the consent-allowlist id
uv run examples/delegation/01_crm_specialist.py
```

An **owner-invoked** message (Bob @mentions his own agent) carries **no**
envelope — `ctx.delegation` is `None` and `ctx.credentials.token_for(...)`
raises the client-side `NoDelegation` error without touching the network.
The example renders that too.

## What the tool demonstrates

```python notest
async def lookup_customer(args: LookupCustomerInput, ctx: ExecutionContext | None) -> str:
    token = await ctx.credentials.token_for(CRM_CONNECTOR_ID)
    # token.access_token acts as the ASKER; use it, never echo it.
```

- **Opting in to context (INT-994)** — a custom tool handler that declares a
  *second* parameter receives the per-message `ExecutionContext`; one-arg
  handlers are untouched.
- **`ctx.delegation` (INT-992)** — the typed envelope: `originator`
  (uuid/handle/display_name), `message_id`, `minted_at`.
- **`ctx.credentials` (INT-993)** — the per-message resolver. It caches by
  audience in memory only, honors the provider's `expires_at` with a 30 s
  safety skew, **never** reuses a token whose `expires_at` is `null`
  (null = unknown, not expired), and single-flights concurrent calls —
  the platform caps exchanges per message (default 20).

## The error contract (first-run funnel)

`token_for(audience)` raises one **typed** error per platform code
(`band.platform.DelegationError` subclasses). The first two are the
**funnel** — they carry remediation text in `str(error)` and mean "fix, then
ask again". The rest are terminal for the triggering message:

| Error | Wire code | Meaning | Next step |
|---|---|---|---|
| `ConsentMissing` | `consent_missing` | The asker has not granted this agent consent | **Funnel:** asker grants consent, then retries |
| `ProviderNotConnected` | `provider_not_connected` | Consent exists, but the asker has no usable provider connection | **Funnel:** asker connects the provider, then retries |
| `WindowExpired` | `window_expired` | Exchange attempted after the processing window (anchored on the envelope's `minted_at`, default 600 s) | New request required |
| `Revoked` | `revoked` | Consent was revoked | Asker must re-grant |
| `AudienceNotAllowed` | `audience_not_allowed` | Connector not on the consent allowlist (or unknown id) | Fix the audience; do not retry the same value |
| `CrossOrgBlocked` | `cross_org_blocked` | Asker and owner are in different orgs | Terminal (hard M2 block) |
| `DelegationDenied` | `delegation_denied` | Deliberately undifferentiated denial — org switch off, connector mode `owner` (see the precondition box), or participants changed | Terminal for this message |
| `ExchangeRateLimited` | `rate_limited` | Per-message exchange cap exhausted | Back off |
| `DelegationUnavailable` | `not_found` | Message unknown / not delivered to this agent / agent not @mentioned — indistinguishable by design | Terminal for this message |
| `NoDelegation` | — (client-side) | The current message carries no envelope (owner-invoked) | Only delegated messages can exchange |

There is **no pending-approval state** — consent is active or revoked, nothing
in between.

## Rules worth knowing

- **Clock starts at `minted_at`**, not at receipt: the exchange window
  (default 600 s, org-tunable) is anchored on the server-minted envelope
  timestamp. A message replayed after a crash may already be outside it.
- **Audience = MCP connector id.** The same UUID that appears on the asker's
  consent allowlist. Not a name, not a URL, not a scope.
- **`expires_at` is the provider's**, not platform-bounded. `null` means
  *unknown* — the resolver fetches fresh every time rather than invent a TTL.
- **No storage.** The platform answers `cache-control: no-store`; the SDK
  keeps tokens in process memory only, scoped to the triggering message.
- **Never echo `access_token`** into a room, a log, or an LLM prompt. Use it
  in the provider call and let it expire.

## Files

| File | Description |
|------|-------------|
| `01_crm_specialist.py` | Bob's agent: a `lookupcustomer` custom tool that exchanges the asker's delegation for a CRM token and renders the full funnel. |
