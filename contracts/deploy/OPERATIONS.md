# Operating the standalone services

Run local commands as the service's Unix user. Databases, backups, exports and
replay output contain private conversation data. Their leaf directories must be
owned by that user and not writable by others; files must be private regular
files with one link. Commands refuse symlinks, corrupt hashes/schema, and an
existing destination. They do not overwrite data to complete a restore.

## Inspection and storage pressure

```sh
contract-service inspect /var/lib/contract-agent/session.sqlite3
```

Inspection opens a committed read snapshot, verifies the journal, derived state
and every snapshot, and prints counts/status without payloads or source. It can
run while the service owns the database. Offline inspection reports unknown
limits when the store does not contain a recorded service limit; it does not
invent capacity values. Authenticated observer HTTP metrics report the live
owner's exact configured limits, database/WAL bytes, free pages, journal count,
and integer pressure percentages. Pressure is warning at 80% and critical at 95%.

The default retention policy keeps the complete journal, invocation results,
snapshots, accepted receipts and content-addressed program artifacts for the
session. Nothing is automatically evicted to admit another turn. The configured
record/page ceilings fail closed and leave accepted state intact. Alert on
pressure before the ceiling and provision a reviewed larger profile or finish
the session and retain a verified archive. Never recycle a session ID or delete
receipts while an old sender may still reconcile an unknown effect. Retention
outside this default requires an explicitly agreed deployment policy.

## Backup and restore

```sh
contract-service backup /var/lib/contract-agent/session.sqlite3 /srv/private-backups/agent-001.sqlite3
contract-service inspect /srv/private-backups/agent-001.sqlite3
contract-service restore /srv/private-backups/agent-001.sqlite3 /var/lib/contract-recovery/session.sqlite3
```

Backup uses SQLite's backup API from a committed read snapshot and works with a
live owner. It verifies the resulting standalone database, fsyncs it, and
publishes it atomically without overwriting a destination. An interrupted backup
does not replace a good backup. Inspect the destination if publication succeeded
but a later filesystem sync reported an error.

Restore only to a fresh destination, with the target service stopped. Keep the
current database intact. Also retain the exact contract package, service
configuration and release manifest, and **all referenced program artifacts**.
Artifacts are immutable and content-addressed: copy the referenced files after
taking the database backup, verify each SHA-256, and preserve their configured
absolute location on recovery. Back up credentials through the secret system,
separately from the journal bundle. A database backup is not a whole-host image.

Start the recovered owner with the same identities and behavior configuration.
Startup verifies snapshots, journal state and source/program hashes; room time
spent offline still counts. Pending accepted IDs and unknown outbox proposals
must retain their exact envelope. A safe retry asks the destination for the
original decision. Do not manufacture a new ID to clear a stuck operation.

An older database backup can omit effects already accepted by the destination.
It is therefore not an automatic rollback mechanism: reconcile every effect
after that backup before resuming. Compatible binary rollback keeps the latest
database and content-addressed artifacts. Unsupported schema/profile changes
fail startup and need a versioned, tested migration that preserves receipts and
unknown-effect status. The first release uses SQLite schema 1; arbitrary legacy
prototype configurations are not silently upgraded.

The installed lifecycle qualification (`../agent-runtime/evidence/production-lifecycle.json`, historical artifact)
checks schema-1 upgrade from `0082d24` to `0683801` and rollback in the other
direction. Stop both room and agent owners, retain their latest databases and
artifact paths, replace the wheel at its stable virtual-environment path, switch
the engine release, then start the room before the agent. Pending model results
and accepted receipts survive both directions without another model call.
This is a compatible stopped-service switch; it does not establish compatibility
for arbitrary future schemas or features. A new agent requires the advertised
durable inbox capability, so do not independently roll its room back to a version
without that capability while leaving the new agent running.

The same drill takes a live agent backup after a destination commit but before
its acknowledgement, restores to a new private path, and reconciles the original
proposal exactly once. It leaves the retired source database intact. Never run
both the restored owner and that retired source as the same participant. It also
exports/replays the recorded program and model results after moving the source
artifacts and provider executable away. Reproduce the drill with two sealed
release bundles:

```sh
python3.12 -m contracts.deploy.qualifylifecycle --baseline /releases/previous \
  --candidate /releases/candidate --state /private/new-lifecycle-directory
```

## Export and replay without execution

```sh
contract-service export /var/lib/contract-agent/session.sqlite3 /srv/private-backups/journal.jsonl
contract-service replay /srv/private-backups/journal.jsonl INVOCATION_ID /srv/private-backups/result.json
contract-service replay /srv/private-backups/journal.jsonl MODEL_ID /srv/private-backups/model-result.json --kind model
```

Export is a private JSON Lines file: one `remote-agent/journal-export/1` header,
then the complete ordered `journal_frame` sequence. The header pins schema,
record count and final hash. Every line ends with LF. Replay verifies the entire
sequence and final hash, matching input/output fingerprints and derived index,
before publishing its private result file. It rejects truncation, duplicate JSON
keys, corrupt framing and conflicting invocation records. The reference bounds
are 2 GiB per archive, 16 MiB per record line and ten million records.

Replay never starts a Python program, calls a model, installs generated code or
sends a room proposal. It works after the source artifact is gone. The result
contains the exact recorded input, raw bounded stdout/stderr and exit status,
plus its decoded result when available. A proposed message in this file is
recorded data; replay does not imply that it was accepted by the room.

Exit 0 means the operation succeeded and, for replay, a complete result is
available. Exit 2 means a configuration/integrity/path check refused the
operation. Exit 3 means the replay file was written but the original invocation
has no usable complete result (pending or recorded failure). This never causes
a fresh invocation. Unexpected service/host failures return 1 with a safe
category. Preserve unknown records for explicit reconciliation or authorized
retry; retrying a model may incur another charge.

## Shutdown and recovery alerts

SIGTERM/SIGINT stop new turns and retain a staged proposal after a bounded
callback finishes. Configure supervisor shutdown time above callback/network
and checker timeouts. An explicit `retry` may resolve a pure computation that
never staged an effect, but it does not erase attempt/call budgets or reset an
unknown room envelope. An exhausted publication budget requires operator review
of the destination receipt; do not edit SQLite rows by hand.

Alert on unready engine/store, poll faults, pending/held work, storage pressure,
exhausted model/send budgets and interrupted/rejected repair. A status-only log
does not replace inspecting the private journal when diagnosing a specific turn.
The service never logs bearer tokens, callback bodies, source code or room text.

Agent observer metrics and `inspect` share the same payload-free diagnostic
projection. `staged` and `send_attempts` identify a retained room proposal;
`execution` identifies a pending program/model computation and its durable
capture status. A `completed` capture is recorded I/O, not a successful room
effect or semantic acceptance. `repair_status` reports the current repair job.
The fixed `events` counters include faults, model/room rejections, held turns,
publication attempts/unknown replies, and repair holds/interruption/rejection
or installation. They count committed journal events across process restarts;
they are not counts of distinct inputs or automatic retry recommendations.
Reason strings and identifiers remain in the private journal. Inspection and
observer requests do not resume work or execute a provider/program.
