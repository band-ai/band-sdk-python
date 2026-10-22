# Contract engine specification

This document defines the pure `session-engine/2` profile for engine `0.2.0-lab.1`, the `room-events/1` profile and the commit requirements of `durable-room/1`. It is independent of a programming language, HTTP framework, database, model or agent SDK. Opt-in features add rules that are specified in their own documents and indexed by the [reviewed profile](profile-v2.md): [text bounds](text-v2.md), [defaults and capacity](defaults-v2.md), [timeout scopes](timeouts-v2.md), [entry-only `new`](lifecycle-v2.md), [host signals](host-signals.md), [rosters and collections](coordination.md), [contribution outcomes](contribution-outcomes.md) and [renewal](renewable-timeouts.md).

## JSON and canonical bytes

Protocol JSON supports null, booleans, valid Unicode strings, arrays, string-keyed objects and integers in `[-9007199254740991, 9007199254740991]`. Floating-point notation, nonfinite values, duplicate keys at any depth, invalid UTF-8 and unpaired surrogates MUST be rejected. `1.0` and `1e0` are unsupported even though they denote integral values. Hosts MUST bound request bytes and nesting before allowing recursive work to exhaust the service.

Canonical encoding uses UTF-8, no BOM, no insignificant whitespace, and keys sorted by ascending UTF-8 byte sequence. Arrays retain their order. Integers have ordinary decimal notation, no plus sign or leading zero; zero is `0`. There is no Unicode normalization. Escape `"` and `\` as `\"` and `\\`; use `\b`, `\f`, `\n`, `\r`, `\t` for their five controls. Other U+0000–U+001F controls use `\u00HH` with uppercase hex digits. Leave `/`, DEL, non-ASCII characters and U+2028/U+2029 unescaped. Literal backslash-u text remains literal; it is not a Unicode escape to reinterpret. Hashes use SHA-256 and lowercase hex output. This is the named profile's canonical encoding, not an assertion of RFC 8785 compatibility.

The reference Python journal uses a separately versioned canonical encoding with lowercase control hex. Implementations MUST NOT replace a private historical journal's encoding and invalidate its existing hashes merely to adopt the protocol encoding.

## Session configuration and trusted context

Initialization takes the compiled contract, session ID, an exact role-to-principal binding map, initial time, cumulative message cap and encoded-message byte cap, plus the trusted host inputs that the contract's declared extensions need. Every role is bound once (a roster role to a nonempty member list; see [collections](coordination.md)), all principals are distinct, and there are no additional roles. Principals are opaque IDs; the host adapter verifies that each bound principal is an agent, because humans are never bound to contract roles ([agents and extensions](agents-and-extensions.md)). Session/principal/message/reference IDs are nonempty valid UTF-8 strings of at most 256 bytes. Identity uses exact strings, without case folding or Unicode normalization.

Times are nonnegative safe integers in milliseconds. The pure engine accepts a trusted `now` with every operation; it does not read a wall clock. An invalid time is rejected as `invalid_time`, and a time before the state's last observed `now` as `clock_regression`. Host events are the exception: they follow the [host-signal check order](host-signals.md#authority-ordering-and-continuity), so an exact retry returns `duplicate_host_event` first, and an invalid time or one before the state's `now` is `stale_host_event`. Duration arithmetic MUST remain within the safe-integer range. Limits are positive safe integers. A session is rejected at initialization if any initial whole-run cap, state timeout, duty or collection deadline would overflow. Message admission additionally checks the target state's deadline additions before changing state.

There is no generic idle timer. The header has no `inactivity-timeout` field (it is rejected as unknown), and silence ends a session only through a declared message, state, collection or whole-run clock ([defaults](defaults-v2.md)). No participant may change its actor, clock, bindings, contract or quotas in a message.

The portable `config` object contains `session`, `bindings`, `now`,
`max_messages` and optional `max_bytes`. Omitting only `max_bytes` selects 16,384
bytes; the exported state always records the resolved value. A contract that
declares extensions also receives the trusted members of the
[v2 schema](schemas/protocol-v2.schema.json) `config`: `capabilities`,
`room_policy`, `max_controls` and `watch_bindings`, plus `initial_presence` from the
[coordination schema](schemas/protocol-coordination.schema.json) for collections. These come from verified host state, never from an agent's request.
An optional `inactivity` member must still be `accepted`, `transition` or
`marked`, but it has no effect because no idle timer exists. The `config` object
is closed: a member not named above MUST fail creation with `invalid_config`
before the checks below, so a misspelled `max_byte` is refused instead of
silently selecting the default `max_bytes`. Initialization
checks the following groups in order and creates no state on failure:

| Order | Failing condition | Error |
|---|---|---|
| 1 | A declared extension that the host did not enable | `unsupported_extension` |
| 2 | An `inactivity` value outside the three policies | `inactivity_policy_required` |
| 3 | Missing/extra roles, duplicate principals or invalid principal IDs | `invalid_bindings` |
| 4 | Missing or failing trusted room policy, watch bindings or `max_controls` for a declared extension | That extension's error ([agents and extensions](agents-and-extensions.md), [host signals](host-signals.md)) |
| 5 | A rule whose recipients, with rosters expanded to their bound members, exceed its [mention cap](contract-dsl.md#mention-caps) | `mention_limit` |
| 6 | Invalid session ID, initial time, message cap or byte cap | `invalid_config` |
| 7 | An initial cap, state, duty or collection deadline exceeds the safe integer range | `invalid_time_range` |
| 8 | `max_bytes` is below the smallest envelope of a reply that a duty or collection can require ([defaults](defaults-v2.md#reserve-known-response-capacity)) | `completion_size` |

Roster bindings and participant attributes are validated before these groups
([collections](coordination.md)). Deployment bindings may require explicit
limits or lower caps before calling the reducer.

### Visible local unchecked results

Only trusted local-reference tooling may select unchecked room policy at session
creation. The persisted `policy_mode: "unchecked"` marker is unchanged by later
calls or restore; hosted profiles reject that mode. For such a session, every
proposal decision (including member-authorization and clock rejections), advisory
decision, contribution-control decision and host-signal decision MUST carry
`policy: "unchecked"`. Exact retries and their stored original decisions retain
the marker. Successful views, host summaries, and externally returned tick
decisions or errors MUST also carry it. Checked sessions MUST omit this marker;
it is not a caller-selected per-call option or proof of authorization. A rejected
operation keeps its existing error and state semantics. The pure tick API still
returns state with `policy_mode`, without adding fields to state or checkpoints;
adapters mark their returned decision/summary rather than mutating that state.

Initial state: `state="new"`, `status="active"`, `version=0`, `outcome=null`, no expiry, no facts/receipts/variables/contributions/history, and zero mentions. `created_at`, `entered_at`, `last_activity` and `now` equal the initial time. Compute `max_at`, the current whole-run expiry, from the mandatory `contract @…` declaration ([contract DSL](contract-dsl.md#mandatory-contract-clock)): the fixed cap, or for a renewable `contract @w max @h` the first window, with the immutable maximum kept in `clocks.hard_at` ([renewal](renewable-timeouts.md)). It is null only for `contract @infinity => expired`. Then enter `new`, including its first visit, duties, state clock and any collection.

## Messages and normalization

A session message is exactly `{id, session, kind, payload}` with optional `expected_version`. Unknown fields, including `actor`, `from`, `to`, `recipients`, `timestamp`, `role`, `clock`, or `metadata`, MUST be rejected. The actor is supplied by the authenticated transport separately. `payload` is an object. `kind` is a string; only declared kinds with an applicable rule are admitted. `expected_version`, if supplied, is a nonnegative safe integer. Remote agent preflight requires it; the pure engine also supports clients that omit it.

Clients MUST generate unpredictable message IDs with at least 128 bits of
OS-CSPRNG output or a keyed derivation retaining at least 128 bits and using a
host-private random key. IDs MUST NOT be derived from payload content, including
payload hashes: IDs can appear in shared transport records even when the payload
is private. IDs share one namespace for the entire session. Another actor's
reuse of an accepted ID is `id_conflict`; it cannot claim the original actor's
duplicate receipt. The pure reducer detects conflicts but cannot prove that
an ID was generated unpredictably. The
[client ID rules](room-clients-v2.md#proposal-ids-private-randomness-shared-namespace)
explain generation, retry and privacy obligations. Predictable IDs in examples
and fixtures are illustrative, never production guidance.

A transport binding MAY supply `id`, `session`, `kind` and/or `expected_version`
outside the JSON body through its deployment-pinned
[`band-contract/carrier/1` map](transport.md#carrier-map-profile). No other
envelope or payload field is carrier-mappable. Omitted field mappings resolve to
`body`. The binding supplies values in a separate `transport` map keyed by the
declared source tokens, not by envelope field names. Neither map is message
content; the actor and clock still come only from trusted host context.

Carrier values are untrusted input and MUST satisfy the same scalar types and
bounds as body values: valid IDs for `id`/`session`, a string for `kind`, and a
nonnegative safe integer for `expected_version`. A supplied value MUST exactly
match the body value if both are present; a conflict is `transport_conflict`.
Merge the supplied values before normal envelope validation and admission.
When a source supplies no value, retain the body field if present; a required
field absent from both places is `envelope`. An absent optional
`expected_version` stays absent, never defaulted to the current version.

Encoding MAY omit only non-body mapped fields actually supplied with values
equal to the normalized message. Encoding followed by decoding with the same
map and supplied values MUST reproduce the same normalized message, including
optional-field presence. Fingerprints, receipts and duplicate detection use
that complete normalized message, never the map, raw frame or source tokens.
The map's immutable identity belongs to the transport binding, not the compiled
contract or pure reducer state. Raw wire size and normalized canonical message
size are separate bounds; both apply at their boundary.

Payload keys MUST exactly equal the declared type's fields, including for empty types. No defaulting, coercion or extra fields are permitted.

| Field kind | Accepted value |
|---|---|
| `text(bytes: min..max, graphemes: min..max, regex: ~r/…/)` | Valid UTF-8 string whose UTF-8 byte length is within the mandatory inclusive byte bounds. When declared, its Unicode **16.0** extended grapheme-cluster count must also be within bounds and the whole value must match the [bounded pattern](safe-patterns.md) ([text](text-v2.md)) |
| `integer(min..max)` | JSON integer within inclusive bounds; a boolean is not an integer |
| `boolean` | Exactly true or false |
| `enum(a\|b)` | Exact string member of the ordered declared values |
| `ref(type...)` | Valid ID; its identity, bound variable and accepted fact kind are checked separately |

An implementation using a newer runtime's Unicode tables MUST still implement the specified Unicode version or declare nonconformance. Byte length, Unicode scalar count and grapheme count are not interchangeable.

## Admission algorithm and precedence

The reducer returns `(decision, next_state)`. It MUST process the following steps in order. Rejections do not roll back an expiry already caused by the initial clock advancement.

1. Validate/advance time and expire the earliest due timer, as below. A clock error returns a rejection and the unchanged state.
2. Resolve the trusted actor to exactly one bound role; otherwise `unauthorized`.
3. Validate the exact envelope (`envelope`) and session equality (`session_mismatch`). Canonically encode it; unsupported payload encoding is `payload`. Enforce canonical encoded size (`message_size`).
4. Compute `fingerprint = SHA256(canonical({message: normalized_message, actor: trusted_actor}))`. Look up the message ID in retained receipts. The same fingerprint returns `{status:"duplicate", original:original_accepted_decision}`. A different fingerprint is `id_conflict`. This precedes terminal/version checks.
5. Require active status (`terminal`). If an expected version is present, require equality with the current version (`stale_version`).
6. Select the unique rule for the actor role and kind from current-state rules followed by `anystate` rules; none is `illegal_message`. The compiler has already prohibited ambiguity. In a [wait state](contract-dsl.md#reply-alternatives), the alternatives of its reply block that share the actor's role and the kind all remain candidates; step 7 picks one.
7. Check exact payload type/fields (`payload`). For every `field=$binding`, require payload[field] to equal the currently bound accepted ID, the fact to exist, its kind to belong to the declared ref kinds, and the sender to be the fact's author or one of its recipients (`reference`). Merely naming another fact of the same kind is insufficient, and so is a bound ID the sender can read in its checkpoint but whose fact it never received. Accepted receipts keep the author and recipients, so an agent can run this check on its redacted checkpoint. In a wait state this step has three parts, in order: (7a) check the payload once against the kind's type (`payload`); (7b) pick the single alternative whose literals match; (7c) check that alternative's `=$` references (`reference`). The compiler has proven that exactly one alternative matches each valid payload, so a compiled object for which 7b could fail is rejected when the source is validated, never at run time, and there is no fallback to the first rule. Every alternative of one kind carries the same references, so the result of 7c does not depend on the branch. Binding, discharge and the transition happen only at commit (step 11).
8. For `each`, reject a sender role already contributing to that rule in this state entry (`already_contributed`).
9. Expand recipients in rule order, remove the sender role, map to principals and deduplicate. A recipient whose bound agent was removed is `participant_removed` ([host signals](host-signals.md)). Check the recipient count against the rule's [mention cap](contract-dsl.md#mention-caps) (`mention_limit`), then the accepted-fact count (`message_budget`). There is no cumulative mention budget.
10. Apply the opt-in [renewal](renewable-timeouts.md) and [collection](coordination.md) checks. Then reserve capacity for the duties that would remain, each with the follow-on its reply will need, and, for an event, the cheapest closing path (`completion_capacity`) and refuse a newly created duty whose full response window cannot fit before `max_at` (`insufficient_time`), as specified in [defaults](defaults-v2.md). Only the reply wait of a stop, an `anystate` head whose alternatives, timeout and every other way out of its wait state (other exits, host triggers that apply there, state clocks) all lead to `end` terminals, is exempt: the cap ends that wait if it comes first. Finally check the target state's deadline arithmetic (`invalid_time_range`).
11. Commit the accepted fact, state changes and original receipt as one pure transition. A durable host must then persist them before exposing acceptance.

A valid duplicate consumes no further message budget, adds no mentions, does not repeat a transition, does not rebind a variable and does not renew any clock. It may arrive after expiry or completion and still return the original acceptance. It must still pass actor, envelope, session and message-size checks. Changing only `expected_version` changes the fingerprint; it is not an identical resend.

There are two results to keep separate: the response decision and the current
state. A duplicate's `original.version`, `original.state` and `original.outcome`
describe its historical acceptance, not the current session. Likewise, a
rejection can accompany an advanced clock or an expiry from step 1. Durable
hosts must inspect the returned state as well as `decision.status`. Transport
credential/membership rejection can occur before invoking the reducer; the
reducer's trusted-actor check does not authenticate a bearer token or API key.

## Accepted transition

An accepted fact stores `id`, `kind`, exact `payload`, `role`, authenticated `actor`, `at=now`, ordered `recipients`, and `rule` ID. Insert it under the message ID. Increment `version` by one and cumulative `mentions_used` by recipient count. Append `{type:"accepted", id, at, role, kind}` to history.

If the rule has `as`, assign its variable to this message ID; a later accepted binding replaces the previous value. Existing facts remain retained. For an `each` rule append the sender role to its contributions. A non-event ordinary rule transitions immediately unless it has `each`; `each` transitions only when all selected sender roles have contributed in this entry. A non-`each` rule with multiple senders means any one of them may take the transition.

Discharge every current-state duty whose required kind and participant include this accepted sender. `all` removes that sender from `remaining`; `any` empties `remaining` on the first qualifying acceptance. Other duties are unaffected. These are duties to produce an accepted message, not duties satisfied by rejected or merely attempted sends.

Accepting a [reply block](contract-dsl.md#reply-alternatives)'s head enters its wait state W and opens W's **wait**: the head's recipient owes exactly one reply before the timeout. The state keeps the wait in `wait`, separate from `obligations`. Every alternative references the head's binding, which nothing in W can rebind, so the reference check (7c) accepts only a reply to the request that opened this wait; the engine never matches a reply to a request by order or structure. The first accepted alternative ends the wait and enters that alternative's target. Leaving W by any path ends the wait, a host transition included, so unlike an ordinary duty the wait never carries over. A rejected reply leaves the wait open, and the replier may send again before the timeout; hosts SHOULD cap rejected proposals per actor per entry into W. Because the alternatives cover every valid reply of their kinds, a valid reply of a listed kind always matches, and there is no new rejection code.

An accepted message renews a clock only when its rule names a renewal target ([renewal](renewable-timeouts.md)); duplicates, reads, rejected inputs, model activity, delivery attempts and outbound mentions renew nothing. A transition to the same state counts as a transition and new entry. The state still records `last_activity`, but no timer reads it.

To enter target `T`, increment `entries[T]` and set `entered_at` to the time of the event that caused the entry: the admitting message's `now`, or the scheduled `at` of the timer that caused it (collection close, offline settlement, host trigger). If `T` is a state with `max_entries N => O` and the incremented count exceeds N, enter terminal O instead. Keep the attempted T count; do not fabricate an extra visit to O. A terminal entry sets `status="terminal"`, `state=outcome=O`, and empties obligations and contributions. A nonterminal entry creates each declared duty with `due_at=entered_at+after_ms` and `remaining=participants`, starts the state's declared clock ([timeout scopes](timeouts-v2.md)), opens its collection ([collections](coordination.md)) and empties contributions. Outstanding duties, including ones carried in by an earlier host transition, disappear on an ordinary message transition and on a collection close ([collections](coordination.md)); a nonterminal host transition preserves them ([host signals](host-signals.md)). No rule, deadline or host transition can enter `new` again ([lifecycle](lifecycle-v2.md)). Variables and facts persist across entries.

`version` counts durable records, not transitions. It starts at 0, or at 1 when a trusted initial presence snapshot is recorded at creation, and increases by exactly one for each accepted fact, each host-control record (every accepted presence or membership report, even one that changes no status, every fired host trigger, and the `control_capacity_exhausted` record), each collection log row (a member settled by offline grace or a contribution control, and each round closed as `settled`, `deadline` or `cancelled`), and the expiry. Entering a state adds nothing. One input can therefore add several: the reply that settles a collection's last member adds its fact and the round's close, and a host trigger that leaves an open collection adds its record and the cancelled round. Restore MUST verify `version` = facts + host-control records + collection log rows + (1 if expired). A duplicate or a rejection adds nothing itself, with one exception: a host event rejected with `control_capacity` still appends the exhaustion record and ends the session `control_capacity_exhausted` ([host signals](host-signals.md)), so that rejection adds one. Records that step 1's clock advance adds before the decision (an expiry, a collection close, a fired host trigger) still count, so a rejected or duplicate proposal can return a higher `version` than the state it was proposed against.

An accepted decision is `{status:"accepted", id, session, role, actor, kind, payload, recipients, version, state, outcome, definition, metadata}`. `definition` is the compiled behavior digest and `metadata` is the rule's attached metadata. Store the exact decision with its fingerprint. Rejections are `{status:"rejected", reason}`. A `payload` rejection caused by a declared field's check also carries `detail`, the first violation found: missing fields, then unknown fields, then value checks, each in field-name (codepoint) order. `detail` has `constraint` and, except for an unknown name over 128 bytes, `field`. The constraints are `required`; `unknown`; `type` with `expected` (`object`, `text`, `integer` or `boolean`); `utf8`; `bytes` and `graphemes`, each with the declared `min` and `max`; `regex`; `range` with `min` and `max`; `enum`; and `ref` (the value is not a valid ID; a well-formed ID that names no admissible message is a `reference` rejection). `detail` is computed from the proposer's own message and the compiled type alone. It never echoes a value and carries nothing from the session, its facts or another role, so a preflight against a redacted checkpoint returns the same `detail`. Every other rejection, including a `payload` rejection raised while canonically encoding the message, has no `detail`. Successful admission does not establish the semantic truth or usefulness of the payload.

## Timers

Active timers are: each duty with nonempty remaining participants; an open reply wait; the current state's clock when declared (entry time plus its duration); pending host-signal dwell triggers; an open collection's deadline and offline grace; and the whole-run cap at `max_at` unless the contract opted out with `@infinity`. There is no generic idle timer. A deadline is due at `at <= now`; sending exactly at the deadline is too late.

Choose the earliest due timer by `(at, category, id)`, with the categories of the [equal-time order](timeouts-v2.md#equal-time-order): work duty, collection deadline, offline grace, state timeout, host trigger, whole-run cap. Within a category, IDs compare lexicographically: `due-10` sorts before `due-2` at the same timestamp, and the numeric suffix is not a numeric tie-breaker. A timer fires with `now` set to its scheduled `at`. The remaining due timers then cascade in the same order until none is due at the observation time, and only then is a proposal admitted. A duty produces its declared terminal outcome, and a reply wait's timeout fires as a work duty with its `due-M` ID and enters its terminal outcome; a state timeout and the whole-run cap produce the outcome their declaration names (`expired` by default for the cap). An accepted renewal moves only the clock its rule names ([renewal](renewable-timeouts.md)). The [recovery-defaults](../contract-lab/test/recovery_defaults_test.exs), [state timeout](../contract-lab/test/state_timeout_test.exs) and [contract timeout](../contract-lab/test/contract_timeout_test.exs) tests and the [portable coordination assertions](tools/checkcoordination.py) pin these ties.

Expiry increments the version once, sets terminal state/outcome, records `expired_at` equal to the scheduled deadline (not the later observation time), and sets `expiry={at,id,outcome,reason[,missing]}`. `reason` is `deadline` for a duty, `state_timeout` for a state clock (ID `state-<name>`) or `lifetime` for the whole-run cap (ID `maximum`); a duty includes its ordered missing roles. Append `{type:"expiry", at, reason, outcome}` to history. The state retains its final protocol facts and duty diagnostics. Further ticks on a terminal state update `now` but never apply another expiry or increment version.

The pure reducer is not a scheduler. A production authority MUST schedule timer advancement while rooms are idle, restore absolute deadlines after downtime, and persist an expiry before announcing it. Wall-clock delays must not extend the original contractual deadline. A deployment MUST define clock synchronization and its maximum tolerated skew. The standalone clock uses UTC milliseconds and a local monotonic floor no earlier than its last durable timestamp; clock rollback cannot revive a durably terminal session.

## Views and preflight

A view first advances time, then authorizes the requesting principal. It returns session ID/state/status/outcome/version, documented role definition, current legal outgoing rules if active (in a wait state, the reply alternatives with their literals and targets, and any `anystate` exits the replier can take from it), visible facts, duties naming that role, the whole-run cap `max_at` (its `idle_at` member is always null in this profile), and cumulative mentions. A collection member's view also lists its pending `contribution_controls` ([contribution outcomes](contribution-outcomes.md)). A fact is visible exactly when the principal authored it or is an accepted recipient. An observer permission for operational counts does not grant these payloads.

The `band-contract/agent-view/1` projection contains `actor`, authoritative `now`, `view`, `checkpoint`, and `checkpoint_hash`. Its checkpoint uses the state schema below but replaces every fact with `{kind}`, removes payloads from original receipt decisions, and sets history to `[]`. Receipt fingerprints MUST retain their original value only when the projected actor authored the message. Every other receipt fingerprint MUST be the fixed string of 64 ASCII zeroes. This keeps occupied IDs available for conflict checks without exposing an offline verifier for a guessable hidden payload. It preserves protocol IDs, binding variables, roles, recipients, counters and timers required for the same admission decision. These identifiers are protocol data visible to bound participants; private payloads and their derived verifiers are not included. One identifier is private: another member's failure control ID in a collection the projected actor does not receive becomes an opaque placeholder wherever it appears ([contribution outcomes](contribution-outcomes.md)). The separate role view contains only authorized fact payloads. Private durable checkpoints retain the original fingerprints unchanged.

Preflight MUST verify the projection profile, bound actor, checksum, contract identities, state shape and explicit expected version. It runs the reducer against a restored redacted state and discards the candidate state. It uses a fresh authority-provided time base, not an unrelated host's monotonic epoch. Its duplicate receipt is intentionally redacted. A local rejection blocks program publication and may trigger LLM takeover; a local acceptance still requires final room admission. The room MUST NOT accept a client-supplied checkpoint as authority.

## Portable state and recovery

A portable state is `{profile, definition, source, state}`. `definition` and `source` bind both compiled identities. `session-engine/2` writes `band-contract/state/2`, which adds host-signal continuity ([host signals](host-signals.md)). Opt-in features raise the profile to `state/3` (renewal and collections), `state/4` (participant types) or `state/5` (contribution controls), each adding its own members ([coordination profile](coordination-profile.md)). A reader MUST reject a state profile it does not support. Every state profile's object contains at least these members:

```text
id bindings now created_at entered_at last_activity max_at idle_ms inactivity
max_messages max_bytes mentions_used state status version outcome expiry expired_at
entries obligations contributions variables facts receipts history
```

All keys and enums are strings. `max_at`, `idle_ms`, `outcome`, `expiry`, `expired_at` may be null as appropriate (`idle_ms` is always null in this profile); there are no language-native tuples, atoms, classes or executable tags. Facts, receipts and variables are maps keyed by IDs/names. A receipt is `{fingerprint, decision}`. An obligation is the compiled duty plus `due_at` and ordered `remaining`. A session of a contract with [reply alternatives](contract-dsl.md#reply-alternatives) also has `wait`: null, or `{id, head, due_at, remaining}` while a reply is owed, where `id` is the wait's `due-M` and `head` is the rule ID of the head that opened it. Restore, the JSON schemas, the codecs and the views include it, and a reader that does not know it MUST refuse the state rather than drop it.

Call the complete `{profile,definition,source,state}` object `state_data`.
Private durable state is wrapped as
`{profile:"band-contract/room-checkpoint/1", data:state_data, hash:SHA256(canonical(state_data))}`.
The hash detects corruption; it is not a MAC and does not authenticate the
checkpoint. Restoring is host authority
([host evidence](agents-and-extensions.md#band-permission-binding-version-1)).
Its 8 MiB limit applies to the canonical bytes of **all of `state_data`**, not
only its inner `state` map, and excludes the outer checkpoint wrapper. The
redacted `checkpoint` member of an agent projection has the analogous 1 MiB
canonical-data limit. That limit excludes the separate public `view`; it is not
a limit on the entire HTTP projection response. A host may impose lower
transport/storage limits and MUST fail closed if a complete checkpoint cannot
be stored or transmitted.

| Boundary | Bytes counted | Limit in this revision |
|---|---|---|
| Session/principal/message/reference ID | UTF-8 bytes of the string | 256 |
| Normalized session proposal | Canonical complete envelope, including supplied ID/session after merge | Session `max_bytes`; omitted configuration resolves to 16,384 |
| Raw wire decoder | Original input bytes, before JSON decoding or carrier merge | Declared decoder bound; reference default 16,384 |
| Private checkpoint | Canonical `state_data` | 8,388,608 |
| Redacted checkpoint | Canonical projection `checkpoint` member | 1,048,576 |
| Full transport response | Encoded response body including view/wrappers | Selected transport's response bound |

All limits are inclusive. Passing one boundary does not imply passing another.
Canonicalization may shorten whitespace/escapes, while state and receipts can
retain several representations of the same accepted payload. Budget exhaustion
does not authorize truncating facts, dropping receipts or sending a partial
checkpoint. See [capacity qualification](operations.md#storage-capacity-and-retention).

Restoration MUST validate exact keys, scalar types/ranges, fixed enums, unique complete bindings, contract/source equality (a mismatch is `contract_mismatch`), state/outcome validity, time ordering, matching fact/receipt IDs, accepted receipt identity, payload schema for full facts, budget bounds, variable references and duty definitions. Unknown keys or enums are not dynamically interned as symbols. A recomputed checksum does not excuse malformed state. Restore advances deadlines to the supplied trusted time. These checks do not replace authenticated origin or durable journal verification.

Portable export MUST be available before moving state between implementations. A source-only contract edit changes source identity and requires an explicit audited migration even if the behavior digest is unchanged.

## Sessionless room events

`room` traffic has no session field and does not enter or advance any session. It has separate bindings and a required host policy: `window_ms`, `max_events`, `max_mentions`, `max_receipts`, all positive bounded integers. The policy may also set `max_events_per_actor`, `max_mentions_per_actor` and `max_receipts_per_actor`, positive bounded integers that default to `max_events`, `max_mentions` and `max_receipts`, so a room without them behaves exactly as before. They keep one sender from spending the whole window, the window's mentions (a fan-out to many recipients) or the whole receipt capacity: an event that would take its sender past its event or mention share is refused `room_budget`, and an actor holding its share of retained receipts is refused `retention_limit`, while the room-wide limits still apply. The event and mention shares reset with the window. A host whose room has members it does not trust SHOULD set all three shares, each below its room-wide total so that other members keep headroom; a share equal to its total isolates no one. The shares bound each sender only: they reserve no capacity for any member and do not stop several members acting together. A missing or non-positive policy value, or a key outside `room_config`, is `room_policy_required`. The portable schema is `room_config`. A room rule's [mention cap](contract-dsl.md#mention-caps) still applies to each event; the host's `max_mentions` is a separate per-window room quota. Room rules are events without transitions, variable binding or reference matching.

The room event authority validates clock and actor, strict common wire encoding and absence of `session`, computes the same actor/message fingerprint, and checks retained duplicate/ID conflicts before new-event quotas. Select the unique room rule, validate payload, compute recipients excluding the sender, then check receipt capacity (`retention_limit`), per-message mentions (`mention_limit`), and window events/mentions (`room_budget`). Accepted decisions contain `status,id,kind,payload,actor,role,recipients,metadata,scope:"room"`. They have no session version or outcome. Callers MUST NOT infer compare-and-swap semantics from an optional common-envelope `expected_version`; it has no meaning for this profile.

The window is anchored at initialization. At time `now`, advance by `floor((now-window_at)/window_ms)` whole windows. If at least one elapsed, reset event/mention counters and preserve the aligned anchor. Receipts are retained across quota windows. Rejections and duplicates do not consume fresh quota. When receipt capacity is exhausted, new events are rejected; old receipts MUST NOT be silently evicted and then permit the same effect twice. Production archival or retention policy must preserve the deduplication contract it advertises. A per-actor share bounds each sender's receipts but frees none; a time-scoped retention horizon is deferred.

## Durable authority and concurrency

Every session/room scope has a single serialized decision order. A standalone owner uses an exclusive local ownership lock and transactional storage. A multi-instance Band deployment MUST lock the authoritative database row or use an equivalent transactional version compare-and-swap; an in-process mutex alone is insufficient. Actor/membership checks, contract transition, receipt and the resulting Band chat message must be part of one authorized transaction.

An accepted or duplicate acknowledgement is returned only after the authority can recover the original receipt. Crashes after commit but before reply are recovered by an exact resend/receipt lookup, with no duplicate effect. A failed commit invalidates speculative in-memory state. Rebuild from the last verified checkpoint and journal; never continue from a state that might have acknowledged an uncommitted operation.

The journal records configuration/identity, semantic state-changing operations, their decisions and periodic full checkpoints. Recovery verifies the chain and checkpoints, replays the tail without republishing messages, and compares decisions/state. Timer-only semantic changes are durable. Pure reads and duplicate/rejected requests that cause no semantic change need not consume journal capacity. No authority may declare readiness after corrupted storage, mismatched configuration, failed recovery or lost ownership.
