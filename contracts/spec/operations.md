# Operations and release specification

Production readiness is profile- and environment-specific. Passing pure-engine tests or building a wheel does not qualify a live model, Linux isolation, a Band deployment or an unmeasured workload. The release manifest and [production ledger](../PRODUCTION.md) must identify the exact qualified artifacts and incomplete requirements.

## Supported deployment shapes

The supported initial runtime is Linux, Python 3.12 and local SQLite storage. Bubblewrap and libseccomp are needed only to isolate the optional experimental runner's generated programs ([remote runtime](remote-runtime.md)). Ordinary agents connect with their own clients and need none of these components ([room/client separation](room-clients-v2.md)). The reference reducer is Elixir/OTP; an independently generated reducer may replace it only after passing the same conformance adapter and checkpoint/decision vectors. Exact runtime and dependency versions belong in the release lock/manifest. Unicode table version is part of the engine behavior and cannot be inferred from a compatible-looking runtime version.

Standalone services consist of a durable room process and, when the optional experimental runner is selected, one or more remote agent hosts. Each owner has a private state directory and separate database. Band deployment replaces the room process/SQLite authority with a Band database-backed authority; remote hosts retain their own independent stores. A room database and agent database are different service identities and cannot be substituted.

Host configuration must explicitly name service/profile, contract package and identities, session/actor/bindings, selected artifact, state directory, quota policy, transport endpoints, TLS material, credential source, control grants, provider/validator configuration and all relevant bounds. Credential values must come from private files/environment/service credentials and never be copied into journaled configuration. Unknown fields, unsafe paths, invalid bounds and partial TLS configuration fail startup. Configuration pinning must compare behavior-affecting values and permit credential rotation separately from contract/state migration.

The outbound-ID host key is an exception to the environment option above: it
MUST NOT be supplied in environment variables or argv. It is generated and
retained in private host state under the lifecycle rules below.

The reference standalone configuration is
[`band-contract/service/1`](schemas/service-config.schema.json), launched by
`contract-service serve CONFIG`. `check-config` validates its structure and
configuration semantics; full startup also verifies referenced material and
state. The [release guide](../deploy/README.md) documents private credential files,
systemd units, polling and signal behavior. Startup emits one `service_ready`
record with kind and bound URL; errors expose only safe categories/validation
messages. Runtime request logs omit bodies and credentials.

## Reproducible installation

A release contains source identity, build identity, package hashes, dependency locks, schemas/specification, conformance fixtures, entrypoints, example configuration, service units, migration/rollback tooling and operational runbooks. Build tooling and runtime dependencies must be declared separately. The Python runtime currently uses the standard library; required OS sandbox/TLS/SQLite components still belong in the deployment requirements.

Build in a clean isolated directory using locked dependencies, deterministic timestamps and verified source. Do not execute undeclared repository helpers or import a developer's home-directory libraries. Verify produced packages in a clean install, launch services from installed entrypoints, and exercise an actual authenticated request before qualifying the package. A source-tree-only demo is insufficient. Published artifact signatures or checksums must bind the tested bytes; local production preparation does not itself authorize publication or deployment.

## Startup, scheduling and shutdown

Before readiness, acquire exclusive ownership; enforce private permissions; validate configuration/artifacts; verify all journal/snapshot state; restore/replay; reestablish authority capabilities; apply offline deadline expiry; inspect interrupted invocations/effects/repairs; initialize bounds; and confirm isolation is usable. Recovery failure must leave readiness false. Liveness can remain true for diagnostics without claiming readiness.

The room schedules idle deadline advancement. Agent hosts have a bounded recovery/event loop that can drain queued work and reconcile outboxes after restart/reconnect without requiring a duplicate user message. Retry/backoff policies must distinguish definitive rejection, unavailable transport and unknown effects. Use stable IDs and persisted budgets. Persistent faults become observable held states rather than a hot retry loop.

Shutdown stops accepting new work, stops schedulers, drains bounded requests/workers, records or preserves unresolved work, terminates owned child processes, closes storage and releases ownership. No detached worker may retain the ability to write a closed or newly acquired store. A shutdown timeout is explicit failure; process supervisors may then terminate the service and rely on durable recovery. Service manager resource limits supplement per-callback limits.

## Storage, capacity and retention

SQLite uses WAL and full synchronous transactions on a supported local filesystem. The main file, owner lock, WAL/SHM/journal companions, snapshots, source artifacts and backups must be private to the service user. Newly created state directories use mode 0700 and files 0600. Existing storage must be owned by the service user, free of final symlinks/hard-link aliases where ownership matters, and not writable by other principals. Do not chmod unrelated parent directories. Shared network filesystem/distributed ownership requires a separately qualified storage profile.

The reference bounds are 100,000 journal records, 1 MiB per record/snapshot and 64 MiB of database pages. WAL, temporary copies, artifacts and backups need additional disk headroom; a database-page cap is not a total directory quota. Monitor total bytes, free disk, record capacity, pending work, WAL and archive growth. Capacity failure must preserve the prior commit and hold new work; it must not discard receipts or pending effects to make space.

Capacity is assessed across boundaries, not by multiplying `max_messages` by
`max_bytes`. Private checkpoints retain facts and original decisions; public
responses add role definitions and visible payloads to the redacted checkpoint;
journals can store output bytes both in history and an index. A legal message
can therefore produce a much larger state or response. The
[engine byte table](engine.md#portable-state-and-recovery) defines what each
portable bound counts; deployment storage/HTTP limits apply in addition.

Qualification MUST exercise the last admissible message, a subsequent timer
expiry, every participant's complete projection, inbox delivery and exact
receipt reconciliation near the selected capacities. A checkpoint limit alone
does not prove that a full view fits, and successful input recording does not
prove that the largest permitted output can be recorded. Admission limits,
reserved headroom or another declared capacity policy must jointly satisfy the
existing durability, delivery and no-eviction requirements. Report any failing
combination as unqualified rather than silently raising a limit or truncating
accepted history. These are required qualification checks, not a statement
that the current standalone and Band implementations have passed every case.

Retention policy is explicit host/operator policy. The default is no automatic deletion of authoritative history. Completed-session archival may move a verified immutable snapshot/history/receipt set to managed storage under a configured policy. Active/pending invocation inputs, unresolved outputs/effects, contract/source versions, migration history and receipts within advertised deduplication retention cannot be deleted. A compacted journal needs a verifiable authenticated/anchored base and equivalent recovery semantics; deleting early rows of the current hash chain is not compaction.

Operator tooling must report capacity before exhaustion, list unresolved work without leaking payloads by default, and export selected invocation/history evidence with explicit private output permissions. The online service may return operational counts; raw journal/backup export requires separate privileged access and is never an observer metric.

The Python reference profile reports a retained proposal (`staged`,
`send_attempts`) separately from pending computation (`execution.producer` and
durable capture `status`). Capture completion is not semantic acceptance or a
room effect. Its fixed `events` counters are committed journal-event counts,
retained across restarts, for faults/rejections, held turns, publication
attempts/unknown replies, and repair outcomes. `repair_status` describes the
current job. These diagnostics contain no input IDs, reason strings, paths,
program state, source, credentials or payloads and must not trigger work.

## Outbound-ID host key lifecycle

This section is for operators of the optional `remote-agent/1` host, not a
requirement to provision keys for ordinary room clients. The key protects the
[unpredictability of new outbound IDs](remote-runtime.md#outbound-proposal-ids).
It is not the actor's service credential and grants no room permissions.

**Reference implementation; release/security acceptance is separate.** The
reference layout is the sidecar `<state-db>.host-key`; the host API is
`HostKey(store).identifier(session, actor, input_id, attempt)`. Credential-grade
backups carry the key in a private `private_host_key` table, separate from
journal records. Restore extracts the sidecar and securely removes that table
before publishing the live database. The built wheel's operator commands have
been checked with removed ephemeral test state, independently of source imports.
These local checks do not establish a merged release or service deployment.

### Generation and private storage

At installation or first start, the host MUST generate exactly 32 bytes using
the OS CSPRNG (`secrets.token_bytes(32)` in Python). First creation MUST be
exclusive (`O_CREAT | O_EXCL`, or an equivalent store transaction), so concurrent
starts cannot install different keys. An existing valid key is reused across
restarts. Host name, actor, configuration, timestamps and deterministic seeds
MUST NOT be used to derive it. Programs, models and room messages MUST NOT
provide the key.

The key MUST remain outside the journal in a file owned by the service user
with mode 0600, inside host-private state, or equivalent private storage that
journal export never reads. Operators should use mode 0700 for the parent state
directory; newly created state directories use that mode. The key is used only
for outbound-ID derivation. Key bytes MUST
NOT enter environment variables, argv, logs, diagnostics, model/program inputs,
room messages or evidence bundles. Even error paths must not print them.
Neither the key path nor a parent exposing it may be bound into a program
sandbox. Callback children MUST inherit no descriptor referring to the key;
the host closes such descriptors and uses `close_fds` when launching them.

The program sandbox does not isolate the model provider command.
`CommandProvider` runs the configured command unsandboxed as the service user,
with the inherited environment, so it can read `<state-db>.host-key` and the
database. Run tool-using or untrusted providers in a separate sandbox that
excludes those files, or as another Unix user without access to them. Otherwise,
the operator must explicitly accept the provider's access to this private state,
including the risk that prompt injection exposes the key and enables pre-claim
denial of service. Closing inherited descriptors alone does not prevent the
provider from opening the key file.

### Exports, backups and restore

| Artifact or operation | Required key treatment |
|---|---|
| Ordinary journal export (`operations.export`) and replay output | MUST exclude key bytes; replay never needs the key |
| Credential-grade host backup (`operations.backup`) | MUST include the key as well as verified database state; the reference embeds it in `private_host_key`, not journal records |
| Restoring a complete host backup | MUST restore the private key for deterministic derivation of new IDs, preserve all stored staged envelopes and verify startup recovery |
| Diagnostics and rotation audit | MAY identify the key by a short fingerprint only; MUST NOT contain the key itself |

Treat a complete backup as credential material as well as private conversation
data: keep owner-only permissions, restrict access and include it in the
deployment's protected secret-backup/recovery process. An ordinary journal
export is not a credential backup or a whole-host restore image. A SQLite-only
backup made by the older command is not evidence that the H7 key was retained.
Never extract a key into logs, shell arguments or hand-edited journal rows.
Restore while the target service is stopped, to a fresh private destination,
and do not run retired and restored owners as the same participant. The target
database and its `-wal`, `-shm` and `-journal` companions MUST all be absent,
including dangling symlinks. Restore MUST refuse stale companions without
deleting them: SQLite could otherwise replay state unrelated to the verified
backup when the restored database is opened.
Restore also clones the outbound-ID credential: if the original and restored
hosts are both retained, rotate one host's key before new traffic. Rotation does
not authorize concurrent owners for the same participant.

The reference restore removes the embedded credential table using SQLite
`secure_delete` and `VACUUM`, but that is not a disk-erasure guarantee. `VACUUM`
uses a rollback journal: after its `-journal` file is deleted, freed filesystem
blocks may still contain the backed-up key. Treat the backup, restore workspace
and possible journal remnants as credential material under the deployment's
storage-protection and disposal policy. The accepted restore procedure preserves
live-key isolation; it does not qualify forensic full-disk hygiene or promise
that deleting temporary files destroys every historical key copy.

### Rotation after suspected disclosure

Rotation is required on suspected disclosure, not on a schedule: this key
protects unpredictability rather than authenticating the actor. The authorized
operator operation MUST generate a new independent 32-byte OS-CSPRNG key,
write a private temporary file, fsync it, atomically rename it into place and
fsync the containing directory. Discard the old live key. Retained backups still
contain their historical keys and must remain protected; a restore from a
known-compromised backup needs rotation before producing new replies.

Record `host_key_rotated` with only a short key identifier/fingerprint, such as
`sha256(key)[:8]`, never key bytes. That fingerprint is not an authentication
token or a substitute for a protected backup. Rotation MUST NOT change a
persisted staged ID, envelope, receipt or unresolved-effect status. Only newly
formed envelopes use the new key; recovery reconciles the exact original
staged envelope, even if the old key is unavailable.

### Lost key and database-only recovery

If restored state lacks the key, the host MUST generate and privately persist
a new key under the same exclusive-creation rules and record a safe key-loss/
replacement event without secret bytes. Operators must distinguish this
replacement from recovery of the original key; future derivations change.
Loss of the key does not require abandoning or regenerating staged replies:
their complete IDs/envelopes are already persisted and MUST be reused.
Reconcile unknown room outcomes using those stored envelopes before producing
any replacement reply. A state/key restore never authorizes replaying accepted
effects or running a second owner.

### Disclosure blast radius

A leaked key lets another participant predict and pre-claim that host's future
reply IDs in the shared session namespace, re-enabling the H7 denial of service
until rotation. Key disclosure alone permits neither actor impersonation nor
data access: the room still authenticates and authorizes the actor independently.
It does not disclose other hosts' independent keys. A stolen complete backup
also contains private conversation state; assess that separate data exposure
as well as rotating the ID key. Room-side protection for careless clients
remains separate hardening; this host key does not implement it.

### Reference operator commands

| Operation | Interface | Reference behavior |
|---|---|---|
| Initial generation / missing-key replacement | Host startup using `<state-db>.host-key` | Exclusive generation; `host_key_created` or `host_key_recovered`, fingerprint only |
| Rotate | `operations.rotate_host_key(source)`; `contract-service rotate-host-key <state-db>` | Atomic replacement; works with the live controller owner; `host_key_rotated`, fingerprint only |
| Backup | `contract-service backup <state-db> <fresh-backup>` | Embeds the key in `private_host_key`; backup is credential material |
| Restore | `contract-service restore <backup> <fresh-state-db>` | Publishes the private sidecar, removes the credential table, preserves staged envelopes |
| Journal export | `contract-service export <state-db> <fresh-export>` | Excludes the key; journal-only format retained |

For example, as the service user, rotation after suspected disclosure is:

```sh
contract-service rotate-host-key /var/lib/contract-agent/session.sqlite3
```

Do not generate a real service key merely to validate documentation. Runtime
qualification uses isolated ephemeral keys, checks exported bytes without
printing those keys, and records command exits and staged-ID recovery separately
from documentation syntax/link checks. Legacy backups made before H7 may lack
the credential extension; restore accepts them and startup records key recovery.

## Backup and restore

Use an online database backup API or a transactionally consistent database-native snapshot, not a copy of an open SQLite main file. Include immutable artifacts, pinned contract source, runtime/configuration identities and separately managed credential/TLS recovery instructions. Never put live credentials in an ordinary journal export.

For H7 remote hosts, this MUST also include the outbound-ID key under the
[credential-grade backup rules](#exports-backups-and-restore). The existing
database backup commands now retain the key when it exists; a pre-H7 backup or
ordinary journal export must not be treated as a complete key backup. See the
reference command surface above.

Build backups as private temporary files, verify database integrity, the complete journal/materialized state and every snapshot, flush data, and publish atomically without overwriting an existing backup. Record hashes and provenance. Failed verification or disk capacity must not publish a success artifact. A backup remains private even when the caller's umask is permissive.

Restore to a fresh destination while the target service is stopped. Verify before publishing, refuse existing target files and corrupt/incompatible backups, then run the same startup gates. A recovery drill must preserve an accepted room receipt, an unresolved outbox proposal, recorded program I/O, state/schema, control deduplication and offline expiry. Measure recovery time and prove no duplicate effects. Band uses its database-native backup/restore and migration tools with the same logical obligations.

The reference operator commands are specified in the
[runbook](../deploy/OPERATIONS.md). `inspect`, `backup` and `export` use a verified
committed read snapshot while the service retains ownership. Export preserves
every journal frame and pins its count/head in `remote-agent/journal-export/1`;
replay validates the complete chain before returning
`remote-agent/recorded-result/1`. It MUST NOT execute programs/providers, publish
messages or install artifacts. A started invocation without an output remains
unresolved. The initial retention policy keeps all history/receipts/artifacts
and rejects new writes at the declared capacity; no automatic deletion or ID
reuse is permitted. Live metrics report storage pressure and exact owner limits.

## Upgrade and rollback

Every durable format and behavior profile has a version. An upgrade must either read the existing version without changing semantics or execute an explicit versioned migration. Before migration, take and verify a backup, stop ownership conflicts, validate capacity, record old/new artifact/configuration identities and run a dry validation. Migration must be transactional or write a verified new store before a controlled switch.

Never rewrite accepted payloads, IDs, actor bindings, receipt fingerprints or unknown-effect status merely to make a new implementation accept old state. A documentation-only source change still changes source identity; migration must prove that accepting the new source does not silently change historical contracts. Legacy Erlang checkpoints are converted inside the trusted old runtime into portable JSON before another language consumes them.

Rollback must specify which old binary can read the current schema. If the old binary cannot, rollback uses a compatible forward adapter or a tested backup/new-store procedure with reconciliation of all externally accepted effects since that backup. Restoring an old database and resending messages is not a safe rollback plan. The release must test upgrade and rollback with pending work, not only an empty store.

## Observability

Expose liveness, readiness, pending queue/outbox/invocations, storage pressure, accepted/rejected/duplicate counts, operation latency, provider calls/tokens/usage, timeouts, repair start/outcome, reconciliation and recovery failures. Metrics must not include payloads, source code, secret tokens, raw IDs with unbounded cardinality, or full journal contents. Logs are structured bounded events with status/category and safe correlation IDs. Error text must not dump subprocess environments, HTTP authorization headers or provider responses containing secrets.

Clock synchronization, credential/certificate expiry, approaching quotas, unresolved effects and failed backups require actionable alerts. Unchanged healthy polling does not produce user-facing messages by default. A model/repair timeout and an unknown accepted effect must be distinguishable to an operator.

## Qualification and acceptance

The qualification manifest records source/build identities, OS/kernel/runtime/dependency versions, filesystem/storage, host resources, credential mechanism (not values), model/provider identity, workload, declared SLOs and exact commands/exit codes. It links all requirement dispositions. Workload/SLO selection must be explicit; a small fixture cannot substantiate unbounded scale.

Required gates include parser/engine/portable schema vectors; independent canonicalization and transition oracle; meaningful regression tests; real sandbox denial/resource probes; crash/commit boundary tests; whole-service restarts; authenticated TLS smoke tests; concurrent authority admission; backup/restore and upgrade/rollback drills; bounded provider faults; semantic live-model qualification; both requested deployment profiles; security/dependency review; and load/soak/recovery measurements for the declared envelope.

Record implementation, review, package qualification, publication, merge and deployment separately. No green unit suite, generated spec, draft PR or unanswered approval question may be relabeled as a deployed, accepted production system.
