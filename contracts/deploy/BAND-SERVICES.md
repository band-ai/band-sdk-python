# Band authoring and supervised program services

The optional Band services ship in the same versioned wheel as the remote runtime.
They consume authenticated production APIs. They do not require the demo checkout,
its database fixtures, a development Band process, or a signed-in personal CLI.
The existing SDK service remains independent of these optional services.

Build a clean release as described in [README.md](README.md). Verify its manifest
with `contracts.deploy.releaseverification.verify_release` before installation. Provision Python
3.12 and the sandbox/TLS/SQLite packages in `requirements-system.json`. Create an
immutable virtual environment outside the sealed release, for example
`/opt/contract/environments/SOURCE_SHA`; do not add files inside the verified
release tree. Install the SDK and its sealed dependencies from the SDK or copied release root:

```sh
python3.12 -m venv /opt/contract/environments/SOURCE_SHA
python3.12 - <<'PY'
from pathlib import Path
from contracts.deploy.installation import install_release

install_release(
    Path("/opt/contract/releases/SOURCE_SHA"),
    Path("/opt/contract/environments/SOURCE_SHA/bin/python"),
)
PY
```

The helper verifies the release, installs the exact dependency wheels from its
`dependencies/` directory using `requirements-runtime.lock`, installs its
canonical `band-sdk` wheel, and runs `pip check`. Package-index access is not
needed. The runtime lock is exported from the SDK's `uv.lock` with the contracts
extra; the retained `requirements-band.lock` records the earlier standalone
host dependencies and is not the canonical installation input. Point
`/opt/contract/current-env` at the installed immutable environment after
qualification; retain the previous environment and matching engine release for
rollback and existing participant replay.

Create separate service users `contract-author` and `contract-programs`. Give each
service its own readable configuration and credential files, owned by that user
and mode 0600 (or provision equivalent private systemd credentials); state
directories must be owned by that service user, mode 0700. Explicit absolute paths
are required. Do not store literal credentials in configuration or unit files.
Use an actual registered agent credential for each enrolled identity. The selected
author must belong to the room and be selected for its authoring job; registering
its ID here does not bypass Band authorization.

## Model configuration

Both services use a fixed executable argv, never a shell command or executable
chosen by room history. A model configuration file has this shape:

```json
{
  "profile": "band-contract/structured-model/1",
  "command": ["/opt/contract/environments/SOURCE_SHA/bin/contract-band-openai", "--model", "YOUR_CONFIGURED_MODEL"],
  "credentials": {"OPENAI_API_KEY": "/etc/contract-author/model.key"}
}
```

`contract-band-openai` reuses the SDK's bounded HTTPS Responses transport. It uses
JSON mode, then the host independently validates the complete requested JSON
schema. Refusals, incomplete answers and uncertain transport outcomes fail closed.
There is no model-selected tool interface. The API policy sets `store: false` and
does not automatically retry model requests. See the
[official output guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
The CLI requires an explicit model; access and limits depend on the operator's
provider account. Its defaults are 8,192 output tokens and 85 seconds, within the
existing worker's 120-second/131,072-byte response limits. Full token usage/cost
is unreported for this command adapter; a call count is not a cost estimate.

A different trusted adapter may be configured. It receives one bounded JSON
request on stdin: `profile: band-contract/structured-model-request/1`, `prompt`
and `schema`. It must print exactly one JSON object and exit. It receives only
the minimum process environment plus explicitly named credential files. Provider
stdout/stderr is bounded; it is not echoed in service errors. The host writes no
parallel raw prompt/event files outside the managed journals. Author/assessment
results are durable; participant turns follow their selected journal policy.

## Selected author

`/etc/contract-author/service.json`:

```json
{
  "profile": "band-contract/author-service/1",
  "state": "/var/lib/contract-author",
  "platform_url": "https://band.example",
  "key_file": "/etc/contract-author/agent.key",
  "model_config": "/etc/contract-author/model-SOURCE_SHA.json",
  "max_model_calls": 20,
  "model_timeout_seconds": 180,
  "poll_interval_ms": 1000
}
```

The author claims `/api/v1/agent/contracts/authoring/claim` and submits staged
results to the same API's job-result route. A claim and its completion retain
their identities through lost acknowledgements. An interrupted model invocation
is held after restart and is not automatically sampled again. The lifetime
model-call budget is stored in the author journal; exhausting it returns a
durable, visible hold. Review or archive that journal before deliberately
provisioning a new budget. The bundled skill and grammar ship inside the wheel.
Band compiles an artifact independently before it can be installed in a room.

If a claimed job's room is deleted or the selected author loses membership, the
platform can refuse its completion with `404 {"error":"not_found"}`. The worker
retains that completion as `scope_unavailable` and continues claiming other jobs.
Its original output, attempt and staged body remain in the journal with
`acknowledged: false`: a lost acknowledgement may hide an already committed result.
The service prints a `protocol_author_completion_held` event when it records the
hold and again on startup, containing identifiers and status without room content.
Generic or malformed 404 responses and transient failures retain ordinary retry
behavior. Current platform authorization still applies to every retry.

Author results require a nonempty source of at most 400,000 UTF-8 bytes, at most
16 unresolved choices and at most 256 derivations. Each choice and derivation
rule must contain 1–4,000 UTF-8 bytes; derivations must cite frozen history IDs.
New invalid output is recorded as failed and reported once as a bounded failure,
without resampling. Older completed output that violates these limits is held
as `invalid_result`; an invalid saved failure envelope is held as
`invalid_completion`. These holds retain the original output, attempt, body and
unknown acknowledgement, while unrelated claims continue. They appear in the
same hold listing and events as scope holds. No saved completion is rewritten
into a different result or failure, and a transport status alone does not create
either local validation hold.

The production author also checks each new result against the actual completion
request encoding and its 1 MiB limit. A result that fits individual fields can
still exceed this limit after JSON escaping; it is recorded as failed and reported
once without another model call. A saved completion that exceeds the request limit
is retained as `request_capacity`, with its exact input, result, attempt, body and
unknown acknowledgement. It appears in hold events and listings while later jobs
continue. The request limit and JSON serializer are unchanged.
A completion response larger than the production reader's 1 MiB limit is retained
as `response_capacity`. The request was sent, so the platform may already have
committed the result. This hold also preserves the exact saved completion and
unknown acknowledgement, and allows later jobs to proceed.

To inspect or retry a held completion, stop the author service and run these
commands as its service user with the same configuration and state directory:

```sh
contract-band-author --config /etc/contract-author/service.json --list-held
contract-band-author --config /etc/contract-author/service.json --retry-held JOB_ID --attempt 0
```

Use the exact job and attempt from the listing. After an operator restores room
access where appropriate, `--retry-held` sends the saved completion once. It
neither claims new work nor calls the model. Only a valid platform acknowledgement
clears the hold; a repeated scope or local capacity error exits 2, while another
transport or invalid acknowledgement error exits 1 and leaves the hold intact. A successful retry exits
0. Both commands take the service's owner lock and refuse to run beside a live
worker. Restart the service afterwards; unresolved holds remain visible and are
excluded from automatic completion retries. A permanently removed room can remain
unresolved indefinitely. Retain its journal for operator review; a local hold does
not establish whether the platform accepted the original completion.
For `invalid_result` or `invalid_completion`, an exact retry preserves the invalid
body and may be rejected again. Inspect the retained journal and platform attempt
before reconciliation; the retry command does not repair or replace the output.
For `request_capacity`, an exact retry remains held while the transport limit
still rejects the saved body; the retry never truncates or rewrites it.
For `response_capacity`, retry only after inspecting the retained attempt and
platform state. An exact retry can clear the hold when a valid bounded
acknowledgement is available; it never invokes the model again.

`model_timeout_seconds` is an optional operator setting, from 1 through 900
seconds; omitted settings retain the 180-second default. Longer comprehensive
revisions can use a larger bounded deadline. A provider command with its own
shorter cutoff must be configured consistently. Room content cannot change this
setting. It does not change participant response deadlines, model-call budgets,
or the rule against automatically repeating interrupted authoring calls.

## Program gateway

`/etc/contract-programs/service.json`:

```json
{
  "profile": "band-contract/program-gateway/1",
  "state": "/var/lib/contract-programs",
  "platform_url": "https://band.example",
  "author_ids": ["00000000-0000-0000-0000-000000000001"],
  "agent_keys": {"Agent:00000000-0000-0000-0000-000000000002": "/etc/contract-programs/participant.key"},
  "engine_command": ["/opt/contract/releases/SOURCE_SHA/engine-band-v1/bin/contract-engine"],
  "model_config": "/etc/contract-programs/model-SOURCE_SHA.json",
  "token_file": "/etc/contract-programs/platform.token",
  "port": 4530,
  "auto_qualification": true,
  "maximum_operations": 16
}
```

Replace all example UUIDs with explicitly enrolled agents. Enroll counterparties
too when owner-triggered test tasks should be sent through their credentials.
The gateway never impersonates an agent using a human key. It binds loopback only;
co-locate it with Band or use an authenticated TLS reverse proxy and restrict its
network access. Configure Band's `CONTRACT_PROGRAM_HOST_URL` and
`CONTRACT_PROGRAM_HOST_TOKEN_FILE` to the same gateway and bearer token.
The platform feature must be enabled separately. A URL, author ID or role binding
does not grant filesystem, tool, control or external-effect access.

The release includes two independent engine processes. `engine-band-v1` is the
hash-pinned `ca1147db8121297eeea7e70db13f0d41d8fee44f` engine used by Band's
current room API and its explicit-state authoring grammar. The current SDK engine
remains at `engine`; it has newer grammar requirements. Use the matching engine
for a session's source and retain it for replay. Do not silently recompile a
historical session against a different engine or rewrite its protocol syntax.

Generic participant startup and automatic qualification accept complete
`band-contract/file-source/1` sources in either frontmatter or standard form.
Standard sources retain the entry, referenced body and package name together in
a private immutable `contract-source.json`. The matching engine from this
release loads it with the trusted host format `file-source` and delegates
validation to the canonical source compiler. The entry SHA remains the source
identity; standard metadata independently pins the body SHA. Full YAML and safe
nested reference paths keep their existing meaning. Retained frontmatter
workspaces keep their exact `contract.band` bytes and service configuration.
Previously failed standard starts that already pinned an entry-only service
configuration need a new runtime workspace; existing pins are not migrated.

For an operator-supplied source envelope, use
`contract-engine /absolute/contract-source.json file-source`, or set the service
engine's `contract` path and `format: "file-source"`. The encoded JSON file is
bounded to 4 MiB before decoding; canonical entry/body limits still apply.
This host option requires the updated engine adapter. It does not change the
portable source formats or the contract authoring CLI's format options.

`assessment_timeout_seconds` optionally sets the host assessment budget from 60
through 900 seconds; the default remains 150. Automatic qualification reserves
40 seconds for isolated validation and gives planning at most 40 percent of the
whole budget. Candidate generation and its one permitted callback correction
share the remaining generation deadline. The provider command may have a shorter
timeout of its own; configure both deliberately. This setting does not extend a
room session, alter qualification examples, or retry a held assessment. Use a new
explicit assessment request after correcting the cause of a hold.

Independent qualification examples are frozen before candidate generation. The
host distinguishes LLM-only, partial/hybrid and proven deterministic coverage.
Only qualified programs can start. An active session with no accepted messages
and no assessment of its own may reuse the latest qualified artifact for the
same room and exact source. Status includes its original assessment session ID;
activation remains explicit and does not generate a replacement or reset any
clock. A session's own pending, held or unqualified assessment takes precedence.
Existing registered policies and historical
arithmetic identities remain supported. Programs retain the SDK's sandbox,
SQLite retention/snapshots, local checks, replay, controlled takeover and repair.
No-journal participants use ephemeral stores and cannot promise restart/replay.

An accepted owner stop persists `stopping` before shutdown begins and blocks
new work and participant recovery. `stopped` confirms the original instance
closed successfully. If the host dies before that confirmation, status remains
`stopping` with a hold and the stop operation retains its unknown outcome. Exact
stop retries can retry closure of an original instance still present in the
host; they never construct another participant to resolve an interrupted stop.
Preserve its journal and inspect physical process cleanup before starting work
in a new session. A stopped participant cannot be restarted by retrying its
original start operation.

Each new participant journals an absolute installed callback path and a model
configuration fingerprint. Keep that configuration file immutable under its
versioned name; credential files may rotate. A changed provider configuration
holds the participant instead of silently changing its model policy. An upgrade
does not migrate existing callback or validator identities. Keep their original
environment, engine, policies and artifacts until those participants finish.

## Start, inspect and roll back

Install the two units from `systemd/`, review paths/ownership, then use ordinary
`systemctl enable --now` for the services being deployed. Their readiness events
are `protocol_author_ready` and `program_host_ready`. The gateway's authenticated
SDK health/metrics endpoints and room program-status API expose runtime state.
Use the [operator runbook](OPERATIONS.md) for journal backup, restore, retention,
uncertain-effect reconciliation and replay without side effects.

Before enabling the client flag/experiment, qualify the author claim/result flow,
history-based generation, Freestyle refinement and a program/hybrid/LLM role on
the intended Band deployment. Test denial for an unregistered agent and an
unauthorized user, a lost acknowledgement, restart and the configured model
budget. Local scripted-provider tests do not prove live model quality or account
access. Roll back by disabling new author/program requests and the client flag,
draining or explicitly holding active jobs, stopping the exact service, and
restoring the previous environment/configuration. Keep journals and pinned old
artifacts; do not reset state or rerun an uncertain operation during rollback.
