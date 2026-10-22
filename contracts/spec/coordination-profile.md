# Portable coordination profile

The local compiler and session engine retain engine identity `0.2.0-lab.1`.
Current partial collections declare `room.contributions/1`. This adds
`band-contract/contribution/1`, `band-contract/collection/2` and durable
`band-contract/state/5`. The [contribution schema bundle](schemas/protocol-contributions.schema.json)
extends the earlier bundles without rewriting them. It separates terminal
member/host control outcomes from accepted business replies and retains bounded
failure evidence and idempotent receipts. See [contribution outcomes](contribution-outcomes.md)
for authority, correlation, limits, privacy and recovery. Older consumers reject
the new extension, compiled fields and state profile.

Earlier coordination profiles remain supported unchanged:
Coordination syntax opts into explicit data profiles: `band-contract/roster/1`,
`band-contract/collection/1`, `band-contract/renewal/1`, and contract
`band-contract/timeout/2`. Their durable state uses `band-contract/state/3`.
Hosts must support each emitted feature profile, not infer support from the
engine string alone. Existing sessions keep their exact source pins, behavior
digests and state profiles; they are not upgraded in place. New sources target
engine `0.2.0-lab.1` and state/2 or its declared coordination descendants.
Retained state/1 fixtures are a temporary checker compatibility boundary, not a
supported new authoring target ([conformance scope](conformance/README.md)).

Reusable participant types add `band-contract/participant/1`, typed rosters
`band-contract/roster/2`, and `band-contract/state/4`. The
[participant schema bundle](schemas/protocol-participants.schema.json) extends
coordination without rewriting the earlier bundle. Named participant schemas
are preserved under `participant_types`; message schemas remain in `types`.
The creation bindings carry validated scalar `attributes`, and concrete role
metadata includes `participant_type` and `attributes`. Type queries and agent
views retain the associated prompts. Earlier readers reject the new compiled
shape and state/4 rather than silently discarding a schema constraint.

The [coordination schema bundle](schemas/protocol-coordination.schema.json)
extends the earlier bundles without rewriting them. It defines collection
bindings and member guidance, collection definitions/results, explicit reset
effects and absolute clocks. Semantic checks still enforce actor uniqueness,
metadata boundaries, references, capacities and timer precedence. A state/2
consumer must reject state/3 and an unsupported compiled feature, never discard
fields it does not understand.

A roster is a parameter of a run, not a rewrite of its source definition. The
reference keeps the original compiled template, instantiates its selectors from
trusted `new` bindings, and persists those original bindings alongside concrete
member bindings. Restore revalidates every participant attribute, re-instantiates the roster and
verifies the binding match. The
source and behavior hashes continue to identify the skill template. A room must
save the template pins and roster atomically and authorize the concrete actors.

The trusted JSON-lines adapter supports `initial_presence` in `new.config`;
this does not add a public endpoint or let an agent supply trusted observations.
The initial snapshot and later presence controls use the room's authority and
clock. There is no claim that Band's public API already exposes this profile.
Dynamic collections are qualified on the session path, not the separate optional
sessionless room-event handler. Public catalog/discovery integration remains the
host work described in [room and client separation](room-clients-v2.md).

Run the portable checks from the repository root after compiling the lab:

```sh
(cd contract-lab && mix compile --warnings-as-errors && mix test --seed 0)
.venv/bin/python -m contracts.spec.tools.checkcoordination
.venv/bin/python -m contracts.spec.tools.checkv2
.venv/bin/python -m contracts.spec.tools.checkspec
```

The current suite asserts source identity, runtime rosters, host and member
contribution controls, failure/offline/timeout
statuses, an active synthesis state, authorized views, optional local checking,
portable restart and contract maximums. It validates emitted JSON against the
current schema, including old-reader rejection of participant types. The v2
checks and the mixed-profile conformance bundle have different coverage;
retained compatibility cases do not qualify the current profile by themselves.
See [coverage and limits](conformance/README.md#coverage-and-limits-of-a-pass).
Local passing evidence does not establish another implementation's
conformance, a merge, or production deployment.
