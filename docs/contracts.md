# Contract rooms and local services

`band.contracts` exposes authenticated contract room operations, author and
program-host clients, and async owners for the optional local services. The
portable HTTP clients use the SDK's supported Python platforms. Local execution
requires Linux, an explicitly selected Python 3.12 interpreter, a matching
contract engine, and the native prerequisites in
[`contracts/requirements-system.json`](../contracts/requirements-system.json).

These examples target this source checkout. Use a release containing these APIs
only after its wheel and engine installation checks pass; their source examples
do not establish installed-distribution behavior. Install the canonical SDK
environment in this checkout:

```sh
uv sync --all-packages --locked --extra dev --extra contracts --python 3.12
```

The public API is `band.contracts`. The same wheel retains `remote_agent` for
existing callback, command, journal, and service identities. Engines and raw
contract fixtures remain versioned assets under [`contracts/`](../contracts/README.md);
the optional runner is described in its
[runtime guide](../contracts/agent-runtime/README.md). Importing the public API
does not start an engine or select local execution.

## Read and propose in a room

Use `ContractConnection` with a `SecretStr` credential and the configured Band
REST URL. `ContractClient.room(room_id, session_id, actor="Agent:...")` verifies
the authority's native protocol, authenticated actor, and session before use.
For a standalone gateway, select `authentication="bearer"` and use
`client.gateway(session=..., actor=...)` with its base URL. Remote URLs require
HTTPS; plain HTTP is allowed only for an explicit loopback IP address.

The [read-room example](../examples/contracts/01-read-room.py) uses
`load_agent_config`, `PlatformSettings`, and the root `.env.test` file. It reads
identity, the compact role view, and one inbox page without logging room content
or credentials. The configured agent must already be authorized for that room
and bound to a role in that session.

`room.definition()` returns the immutable actor-visible definition;
`room.view(hydrate=True)` includes it when absent from the compact view.
`room.inbox(after=cursor)` returns native receipts and a native cursor. Persist
the cursor only after durably handling its page. Reading the inbox does not
acknowledge ordinary Band chat. A cursor conflict requires reconciliation.

To send a message, create one `room.proposal(id=..., kind=..., payload=...,
expected_version=...)`, retain it, then `await room.propose(proposal)`. The ID
must be unpredictable and the kind, payload, and version must follow the
authenticated room's rules. The proposal snapshots its encoded body. If delivery
is unknown, retry that same identity and exact body; do not generate a new ID or
resample a model. Applications own durable request retention across restarts.
The room response distinguishes `accepted`, `duplicate`, and `rejected`;
transport failure does not establish rejection. The room remains authoritative.

`contract_tools(room)` supplies ordinary participant tools for an existing
adapter's `additional_tools=`. It binds those tools to the verified room and
session. Operator controls and program-host grants remain separate.

## Own a local service's lifetime

`ContractService`, `ContractAuthorService`, and `ContractProgramService` accept
an existing private JSON configuration and `RuntimeConfig(python=Path(...))`.
The interpreter path is absolute and must refer to a Linux Python 3.12
environment containing this SDK. The local runner uses isolated Python startup
and an explicit environment; it does not inherit ambient provider credentials.
Provision credentials through the selected service's supported private files or
an explicitly configured runtime environment.

Use `async with service:` to await its actual readiness event and close the
owned process on exit. `await service.wait()` waits for its confirmed exit.
Cleanup drains output and joins the child; abnormal exits or forced termination
raise `ContractProcessError`. Configure shutdown deadlines above the bounded
operations in the service configuration. Reusing a closed instance is not a
service restart; construct a new owner with the same retained configuration.
See the [managed-service example](../examples/contracts/02-managed-service.py).

`ContractService.check_config(config, runtime=runtime)` validates the ordinary
room/participant configuration. Starting a service also verifies its referenced
files, identity, ownership, and runtime prerequisites. The
[deployment guide](../contracts/deploy/README.md) and
[Band services guide](../contracts/deploy/BAND-SERVICES.md) document the full
configuration schemas, engine selection, credentials, and operational bounds.

The installed `band-contracts --python /absolute/runtime/bin/python` command
provides the ordinary `check-config`, `serve`, and journal operations with the
same explicit interpreter qualification. The retained `contract-service`,
`contract-band-author`, and `contract-band-programs` entrypoints serve existing
deployments from the same SDK wheel.

## Author, program host, and journal operations

`ContractAuthorClient` uses a Band agent API key for explicit stable claims and
`AuthorCompletion` submissions. `ContractProgramHostClient` requires a platform
bearer grant and a trusted `ProgramScope`; a participant credential or room
message cannot create that authority. Both support async context management.
Their request models preserve encoded bodies for exact retries. They do not
replace the retained services' durable journals.

`ContractAuthorService.list_held()` and `retry_held(job, attempt=...)` run
exclusive maintenance only after the service is closed. A retry sends the saved
completion and never invokes the model. The original service owner lock also
prevents maintenance beside a separately running worker.

`ContractJournal(path, runtime=runtime)` exposes inspect, search, backup,
restore, export, replay, and host-key rotation through the installed runner.
Journal pages retain their `through` head for stable pagination. Restore and
export destinations must be new; replay verifies recorded I/O without executing
programs or models. These operations have the storage and ownership rules in
the [operator runbook](../contracts/deploy/OPERATIONS.md).

The retained [implementation](../contracts/IMPLEMENTATION.md) and
[production](../contracts/PRODUCTION.md) ledgers describe historical checks and
their limitations. They are not qualification of an arbitrary SDK build,
provider account, Band deployment, or target host.
