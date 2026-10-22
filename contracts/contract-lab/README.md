# Elixir contract lab

Current framing: **multi-agent skills** combine retained prompts and guidance with an enforceable contract. Read [multi-agent skill guidance](../spec/multi-agent-skills.md), including runtime participant collections, partial results and explicit renewable clocks.

Current partial collections use `room.contributions/1` and declare their successful
reply shape. `Engine.stop_contribution/3` lets an authenticated member stop its own
contribution; `Engine.fail_contribution/3` accepts a trusted host report for terminal
execution failure. Neither requires a custom failure message from the agent.
See [the contribution protocol](../spec/contribution-outcomes.md) for scoped reports,
cause types, retries, privacy, capabilities and state/5 recovery. Old collection/1
sources and checkpoints retain their existing behavior.

The room engine accepts messages from any compatible authorized client. No
client journal or local checker is required. Run `mix run examples/direct_agent.exs`
for discovery, rejection, correction and exact retry through the existing host
interfaces. This local demonstration does not qualify public Band discovery.
See [room/client separation](../spec/room-clients-v2.md) and
[preferred lowercase keywords](../spec/lowercase-v2.md).

**Profile:** the lab implements engine [0.2.0-lab.1](../spec/profile-v2.md) only. Start with [the assistant example](contracts/v2/assistant-answer.band). Sources are exact UTF-8 files, roles bind agents, text is byte-bounded and host extensions are declared. [The DSL specification](../spec/contract-dsl.md) is normative; this README summarizes it.

A request that waits for its answer lists the replies it accepts and a timeout:
`lead -> reviewer: request() awaits reviewing`, then `=> review() & collected`
and `| @5m & stalled` on the following lines ([reply alternatives](../spec/contract-dsl.md#reply-alternatives)).
Optional state and whole-run limits use `state reviewing @5m => stalled` and
`contract @30m => expired`. Read the [timeout scopes](../spec/timeouts-v2.md)
and [complete scoped example](contracts/v2/scoped-review.band) for clocks,
group obligations, capacity, re-entry and restart behavior.


Local reference implementation for the experimental multi-agent skill profile `0.2.0-lab.1`. Contracts use YAML frontmatter, either in the `.band` file or as a standard skill package. The older Python profiles are unchanged.

The compiler, query layer, runtime, journaled replacement controller, room recovery example, and authoring skill family are implemented locally. See [the expectation ledger](../IMPLEMENTATION.md) for evidence and integration limits.

```bash
cd contract-lab  # from the repository root
mix deps.get
mix test --seed 0
```

The sibling header parser brings its pinned YAML dependency. Tests use the lightweight room simulator; no Band service, database, or model is needed.

## Contract corpus

| Contract | What it exercises |
|---|---|
| [assignment](contracts/assignment.band) | Offer acceptance, correlated references, independent review, requester acceptance, questions, bounded revisions, cancellation acknowledgement |
| [team-review](contracts/team-review.band) | Two workers can submit in either order before review |
| [all-participants](contracts/all-participants.band) | Every participant must acknowledge |
| [first-responder](contracts/first-responder.band) | Any one selected participant can answer |
| [selected-deadlines](contracts/selected-deadlines.band) | Different participants have different deadlines |
| [progress-events](contracts/progress-events.band) | Any-state progress and a separate state deadline |
| [bounded-review](contracts/bounded-review.band) | Explicit limit on repeated state entries |
| [room-events](contracts/room-events.band) | Typed sessionless room events |
| [program-counter](contracts/program-counter.band) | Deterministic program inputs and outputs for recovery tests |
| [hybrid-assistant](contracts/hybrid-assistant.band) | Remote Python common cases, explicit language delegation, and room-authoritative answers |
| [agent-control](contracts/agent-control.band) | Typed sessionless sidecar activation, separate from ordinary program inputs |
| [history-review](contracts/history-review.band) | A documented contract derived from a synthetic conversation, with executable positive and negative traces |
| [history-review-v2](contracts/history-review-v2.band) | An explicit later policy revision, preserving the original contract for old sessions |
| [explicit-infinity](contracts/explicit-infinity.band) | `contract @infinity => expired`: an explicit opt-out of the whole-run clock |

The [v2](contracts/v2) and [v3](contracts/v3) directories hold the specification's worked examples, including scoped clocks, collections and renewal.

## Documentation is metadata

A triple-double-quoted string attaches to the next declaration. Blank lines and comments can separate them. There is no documentation directive.

```text
"""
The worker must accept within two minutes or the task is unclaimed.
"""
lead -> worker: offer() as task awaits offered
  => accept(task=$task) & working
   | @2m & unclaimed
```

A one-line `"..."` string is the usual form. Documentation can precede `contract`, `role`, `type`, a field, `state`, a rule, a collection, `anystate`, `room` or `end`. Two unattached strings, an orphan string, and an unterminated string are errors.

The compiler uses Elixir's literal parser for heredoc indentation, escaping, and the final newline. Interpolation is rejected. The closing delimiter is on its own line. This is a literal-only subset of [Elixir heredocs](https://github.com/elixir-lang/elixir/blob/main/lib/elixir/pages/references/syntax-reference.md#strings); parsing never evaluates code.

```elixir
{:ok, contract} = ContractLab.load("contracts/assignment.band")
{:ok, type} = ContractLab.query(contract, {:type, "offer"})
IO.puts(type.meta.doc)
IO.puts(type.fields["task"].meta.doc)
{:ok, worker_view} = ContractLab.for_role(contract, "worker")
IO.puts(worker_view.role.meta.doc)
```

`query/2` accepts the full contract, `{:type, name}`, `{:state, name}`, `{:outcome, name}`, `{:role, name}`, `:room`, or `:any_state`. Role views preserve documentation on relevant rules and duties, all types and outcomes, and the contract itself. They are prompt aids, not a confidentiality boundary.

Metadata contains `doc` and a one-based body `line`; absent docs are `nil`. `source_digest` is the SHA-256 of the exact file, header and body ([file identity](../spec/file-identity.md)). The behavior `digest` excludes attached docs and source locations. This experimental profile uses sorted UTF-8 keys, integer values, and compact JSON; it does not claim full RFC 8785 numeric conformance.

## Body declarations

`load(path, format)` reads an exact file. `format` is `:frontmatter` (the default) or `:standard`; the retired native header fails with `frontmatter_required`; any other format is refused (`invalid_format` in the Elixir API, `input` on the portable source envelope).

- `contract @window => outcome` is required once and sets the whole-run clock, measured from creation. `contract @80m max @4h => expired` makes it renewable under a fixed ceiling, and `contract @infinity => expired` opts out. A source without this line fails with `contract_clock_required`. The line also attaches contract-level documentation.
- `role name` optionally documents a card role.
- `type name` declares payload fields on the following lines. Types are `text(bytes: min..max, graphemes: min..max, regex: ~r/…/)` (the byte bound is required, the rest optional), `integer(min..max)`, `boolean`, `enum(a|b)` and `ref(message_a|message_b)`. No fields means an empty payload.
- `state name` declares a state. `new` is initial and no rule may return to it. `state name @5m => outcome` caps one state entry, and `max_entries n => outcome` bounds visits, including self-transitions.
- `from -> to: kind() => next_state` declares a transition. Role selectors are a name, `{a,b}`, `~w[a b]` or `*`. Routing excludes the sender.
- `from -> to: kind() awaits wait_state` is a request that waits for one reply. The following lines list the alternatives: `=> reply() & next_state` first, `| reply(field=value) & other_state` for each further reply, and `| @5m & outcome` last as the timeout. The reply picks the next state, and it must reference the request (the compiler supplies `as` and `=$` when neither is written). A head has one sender and one recipient. Another `=> wait_state` with the same route and kind re-sends the request. A request answered by a separate rule in its target state is refused with `reply_required`; write the answer as an alternative instead.
- `as binding` binds an accepted message ID. Every reference field must match `field=$binding`, with its declared nominal type checked against accepted session facts.
- `each` waits for one message from every sender in the selector before transitioning. Other transitions need one sender.
- `from -> to: kind() @5m => next_state | outcome` is a timed rule: the message is owed within five minutes of entering the state, or the session ends with `outcome`. Use it for an owed report that answers no request, such as a deliverable after the worker's own acknowledgement; an answer to a request is a reply alternative. Durations use positive integers in `s`, `m`, `h` or `d`.
- `event` leaves the state unchanged and has no target. `event from -> to: kind() @5m => outcome` owes one such message per state entry without moving the state.
- `mentions N` (or `mention 1`) caps the principals one message reaches. On the `contract` line it sets the default; on a rule it overrides it. With neither, there is no cap.
- `anystate` supplies rules in every active state. Terminal sessions remain terminal.
- `room` supplies sessionless event rules. Bindings and state transitions are unavailable there.
- `end outcome` declares a terminal outcome. `expired` exists without a declaration. Terminal names have no implicit success meaning.

The compiler checks unique declarations, known roles/types/states, reference matches, ambiguous rules, satisfiable deadline actors, mention caps on fixed recipients, and valid bounds. It does not prove general asynchronous liveness, confidentiality, or external artifact truth. Binding availability is checked at runtime; the compiler verifies a binding declaration exists, not that every possible path reaches it.

No executable code or contract-specific branch belongs inside a contract. A role's implementation is a separate program.


## Session runtime

```elixir
{:ok, contract} = ContractLab.load("contracts/assignment.band")
agent = %{kind: :agent, active: true, permission: "participant"}
policy = %{revision: "room-1", fixed_roles: %{},
           principals: %{"alice" => agent, "bob" => agent, "carol" => agent}}
{:ok, session} = ContractLab.Engine.new(contract,
  session_id: "job-1", now: 0, room_policy: policy, max_messages: 1000,
  bindings: %{"lead" => "alice", "worker" => "bob", "reviewer" => "carol"})
message = %{"id" => "offer-1", "session" => "job-1", "kind" => "offer",
            "payload" => %{"task" => "Build and verify the widget"}}
{decision, session} =
  ContractLab.Engine.step(session, message, actor: "alice", now: 1, room_policy: policy)
true = decision.status == :accepted
{:ok, session} = ContractLab.Engine.tick(session, 120_001)
"unclaimed" = session.outcome
```

The trusted host supplies actor identity and monotonic integer milliseconds. The role binding is exact and actors must be distinct. Message bodies cannot claim actor identity. A rule derives recipients, removing the sender. Payloads have exactly the declared fields; text bounds are UTF-8 bytes, with optional grapheme and pattern limits. Integer and clock values use the JSON safe integer range.

Every member call carries the host's room-policy evidence as `room_policy:`: each bound principal must be an active agent member with the required permission. Without it, `new`, `step` and `view` fail with `host_policy_required` ([agent scope](../spec/agents-and-extensions.md)). Only a local reference adapter can start a session in the explicit unchecked mode, and its results are marked `policy: "unchecked"`.

The `contract` line sets the whole-run clock at creation; there is no idle timer. State clocks cap one state entry, and timed rules create duties when the state is entered. At equal times a work duty fires first, then a collection deadline, an offline grace, a state timeout, a host trigger and the whole-run cap ([timeout scopes](../spec/timeouts-v2.md)). Time advances before proposal validation, so even an invalid proposal can cause a due timeout. An event can discharge a matching duty; it extends a deadline only when its rule declares a [renewal](../spec/renewable-timeouts.md). An `each` rule accepts one contribution per sender and transitions only after every selected sender contributes.

Accepted message IDs are immutable. An identical retry returns its original receipt, including after expiry. A changed retry conflicts. Retries do not consume quotas again. The host supplies a finite `max_messages`; a `mentions N` cap limits the recipients of each message, and there is no session-wide mention budget. A terminal name is a named outcome, never an implicit success flag. External work may still need reconciliation after expiry or cancellation.

`Engine.view(session, actor: actor, now: ms)` returns legal moves and a documented role view, plus facts authored by or addressed to that actor. It also advances due timers. `snapshot/1` and `restore/3` are trusted-host APIs; restoration checks the pinned contract and rechecks deadlines. This pure reducer does not itself schedule wall-clock timers, persist decisions, publish messages, or authenticate a network connection.

## Wire normalization and room events

`ContractLab.Wire.decode/2` rejects duplicate JSON keys (including nested objects), trailing input, floats, integers outside the safe range, and undeclared envelope fields. A trusted transport may supply `id` and `session`; a contradictory body value is an error. `encode/2` can omit exactly those supplied values for a round trip. Actor identity remains outside the JSON document.

```elixir
{:ok, message} = ContractLab.Wire.decode(
  ~s({"kind":"ping","payload":{}}),
  transport: %{"id" => "room-message-1", "session" => "session-1"})
```

`ContractLab.RoomEvents.new/2` requires explicit `window_ms`, `max_events`, `max_mentions`, and `max_receipts` (optional `max_events_per_actor`, `max_mentions_per_actor` and `max_receipts_per_actor` default to the room-wide values), alongside role bindings and host time. `step/3` accepts only declared `room` events without a session key. Its quota resets on fixed window boundaries; duplicate receipts persist across windows. Reaching the receipt-retention cap rejects new events rather than forgetting old identities. This local profile does not implement archival or identity-epoch rotation.

The transport integration test uses real room-simulator clients and queues: a room-bound sender posts, the host delays and duplicates the notification, and the engine admits the logical input once. Stored posts remain transport data; they are not accepted contract facts until the engine returns its decision.

## Event journal and snapshots

`ContractLab.Journal.start_link(path: path)` opens one journal process. `append/2` writes one complete JSON record and synchronizes it before returning. Each record includes a sequence number, the preceding record hash, and its own hash. `read/1` returns the verified records. These hashes detect accidental corruption; they do not authenticate a writer who can rewrite the entire file.

`snapshot/2` writes JSON state with the current journal sequence and hash. It synchronizes a temporary file and replaces the previous snapshot by rename. `latest_snapshot/1` checks both its checksum and its position in the surviving journal. A missing or damaged snapshot can be distinguished from a damaged journal.

On restart, a partially written final record is discarded only after the complete prefix passes verification. A corrupt completed record stops recovery and preserves the file for diagnosis. Defaults bound the journal to 64 MiB and snapshots to 4 MiB; both limits are configurable. A failed capacity check leaves existing data intact.

Ownership is enforced per normalized path within one BEAM instance. Symbolic-link paths are rejected. This profile requires a single host process to own its files; it does not provide a cross-process file lock, replica election, journal compaction, or a qualified power-loss guarantee. The program harness uses this journal for local process-crash recovery.

## Program replacement and recovery

`ContractLab.Replacement` owns one participant's journal and routes accepted deliveries into a `ContractLab.Program`. A program implements `init(role_view)`, `state_version()`, and `handle(accepted_event, state)`. State is a JSON map. A successful handler returns `{:ok, new_state, intents}`; an unfamiliar input can return `{:unhandled, reason}`. The program receives contract documentation through its role view.

Callbacks run in monitored worker processes. A timeout stops the worker, and a guardian stops it if its owning controller dies. This is process supervision of trusted application code, not a security sandbox. Callbacks must be deterministic and return effect intents instead of performing external effects directly.

The controller writes an input before running it. It then commits the resulting state hash and outgoing intents together in one journal record. Snapshots are taken after a configurable number of completed inputs. On restart, the controller restores a verified snapshot and replays the completed tail. Replay checks state and intent equality and does not publish outputs; effect keys are assigned once, when a completion is first journaled, and are never recomputed. Outgoing effects remain in an outbox with stable keys and destination receipts.

Effect keys are HMAC-SHA256 under a private per-host key, `<journal>.host-key` (32 bytes, owner-only), so a co-recipient cannot predict a room message ID. The journal records the key's public id, never the key. **The key is credential material that travels with the journal: back it up and restore it together with the journal and its snapshots.** A restart with the key missing after any effect was journaled fails with `host_key_missing` and creates no key; once the journal has recorded a key id, a different key file fails with `host_key_mismatch`, with or without effects, and only adopt (below) commits it. A journal with no key id yet (written before key ids were recorded, pre-O1, or a first start cut short before its key id was journaled) commits whatever key file is present at its next start. Only a journal with no effects yet may get a new key, and its id is journaled. Before a journal first commits to a key, the key is made durable: creating one syncs its directory after linking it into place, and a present key that is not committed yet is synced together with its directory. If that sync fails, the start fails closed, and so does every later start until a sync succeeds.

Other start errors for the key and the journal:

- `invalid_host_key`: the key file is not a 32-byte regular file with one link, mode 0600, owned by this OS user, or creating or syncing it failed. Repair the file's mode, owner or links; never loosen its checks. Remove it only while the journal has no effects: with effects, a removed key is a lost key (see below).
- `sync_unavailable`: neither `/usr/bin/sync` nor `/bin/sync` is an executable file. Creating a key runs coreutils `sync -- <dir>` to make its directory entry durable, so a Replacement checks for it at boot; it is never looked up through `PATH`.
- A crash inside key creation leaves only its private staging directory (`.<key file>.staging-<nonce>`). The next start removes it, together with a staged second link to the key.
- While the journal has no key id yet, a present key that was never committed (a crash between linking the first key and journaling its id) is committed at the next start. Once an id is journaled, the same crash while replacing a lost key leaves a different key: the start fails with `host_key_mismatch`, and adopt commits it.
- `missing_initial_state`: the journal has records beyond its configuration and host-key records but no initial state. A start whose program init failed does not cause this: the next start retries the init. Treat the journal as damaged and restore it from a backup.

**Adopting or rotating the key (operator op).** With the participant stopped, run `mix contract.host_key adopt JOURNAL [--journal-max-bytes BYTES]`, or call `ContractLab.Replacement.adopt_host_key/2`. Pass the participant's `journal_max_bytes` when it is above the 64 MiB default, or adopt fails with `journal_limit`. In a release, the `contract-engine` wrapper takes only a contract path, and `bin/contract_engine eval` exits 0 whatever its expression returns, even `{:error, :owner_conflict}`. Use this exact command instead:

```sh
bin/contract_engine eval '
  {:ok, _} = Application.ensure_all_started(:contract_lab)
  [journal | max] = System.argv()
  opts = Enum.map(max, &{:journal_max_bytes, String.to_integer(&1)})

  case ContractLab.Replacement.adopt_host_key(journal, opts) do
    {:ok, result} -> IO.puts(JSON.encode!(result))
    {:error, reason} -> IO.puts(:stderr, "adopt failed: #{inspect(reason)}"); System.halt(1)
  end' JOURNAL [JOURNAL_MAX_BYTES]
```

It prints the result as JSON and exits 0, or prints the reason on stderr and exits 1. When the journal cannot be opened, an Erlang crash report for the journal may be printed first. Adopt commits the journal to its key:

- A present key is synced with its directory and journaled as `adopted`, or reported as `unchanged` if it is already committed.
- A missing key is created and journaled as `created`.
- It takes the journal's owner lock, so it refuses with `owner_conflict` while the journal is in use. When another OS process holds the journal, this comes after waiting about 5 s for the lock.
- It refuses a journal without a configuration record with `configuration_missing`.

Journaled effects keep their keys, because replay never recomputes them, so only new completions use the committed key. Use it for:

- **Upgrades.** A journal written before outbox keys were HMAC (before set O) that has effects and no key file fails with `host_key_missing`. Adopt creates its key.
- **A lost key.** A journal restored without its key also fails with `host_key_missing`. Adopt creates a new key. Only effects journaled in the restored journal keep their keys, so only their retries send the same IDs. An input completed after the backup was taken is executed again under the new key, so its destination may see a duplicate: losing the key loses idempotency for redelivered inputs.
- **Rotation or `host_key_mismatch`.** Move the old key file away first, or keep the present file deliberately, then adopt. Effects already journaled are not re-keyed.

A crash or an unhandled input invokes the configured `sidecar` callback. The callback gets the fault, documented role contract, snapshot, journal path/hash, last 20 records, pending input, and unresolved effects. The full history remains in the journal, available for selective reading. The callback returns `{:ok, replacement_module}`. Before switching, the controller checks its state version, verifies the original initial state, replays **all** completed history against the candidate, and probes the failed input. A compatible candidate resumes automatically. Repair calls have a durable bound that survives restarts. A missing sidecar or exhausted repair budget leaves the input queued for intervention.

`activate/2` lets explicitly configured control actors wake the sidecar. Raw room posts never count as accepted deliveries; a trusted host must validate them with the contract engine and provide the resulting receipt. Receipt validation checks contract/session, attributed actor/role, recipients, and payload schema. This is an in-process host API, not a signed network receipt format.

Room intents have `type: "room"`, `kind`, and `payload` fields. The controller checks their schema and sender role; the destination adapter must admit them through the current contract authority before publication. External intents have `type: "effect"`, `action`, and `payload`, and require an explicit host action grant. Contract prose cannot grant tool permissions.

`flush/3` records an effect as started before calling a destination. A destination must return a receipt. If the outcome is unknown, a non-idempotent effect is held for `reconcile/3`; it is not blindly executed again. An adapter explicitly configured as idempotent can retry with the same key, within its attempt limit. Adapter identity and its idempotency claim are pinned on the first attempt. Completion receipts suppress resend after controller restart. A full journal returns a blocked-outbox error.

The current profile requires the same state-version format across automatic repair. A schema change requires an explicit migration; it cannot silently reset state. Restart also requires the selected program artifact to be available through the configured program registry. A program fingerprint identifies its module and loaded BEAM code digest; it is not a trust signature. The live Band adapter and a real model provider are separate host integrations.

## Connecting a code-generating sidecar

`ContractLab.CommandSidecar.new/1` builds a sidecar callback for a configured local generator command. Supply `workspace`, `executable`, `args`, a required `validate: {executable, args}` command, and `timeout_ms`. Arguments can contain `{bundle}`, `{output}`, or `{candidate}` placeholders. Commands receive arguments directly, without a shell. This Linux adapter uses the installed GNU `timeout` command to bound subprocess execution and captures at most 64 KiB of command output.

The generator reads the fault bundle and writes one Elixir module to the output path. The bundle supplies a fresh `candidate_module` name. Redefining an existing program module is rejected. Candidate source must be UTF-8, at most 128 KiB, and contain one module. The configured validation command must succeed before the code is compiled and exposed to the replacement controller. The controller then applies its own history-replay and failed-input checks.

Successful artifacts retain source, compiled BEAM bytes, content hashes, and the validation receipt under `workspace/releases`. `load_programs(workspace)` verifies those bytes and returns modules for a controller's restart registry. Loading a different definition over an already loaded module is rejected. The workspace and generated code are trusted host inputs; validation is not an OS sandbox. Candidates compile and run in the host VM, so enable the sidecar only with a trusted generator. The adapter keeps the workspace private (mode 0700), and `load_programs` refuses a release that is not owned by the host user or that group or others can write (`artifact_permissions`). No meeting text or room message selects an executable command.

The adapter accepts an LLM-backed generator without depending on a particular provider. Its tests use a local scripted generator that really writes and validates code. They do not make a live model call or measure a model's token bill.

## Run the room recovery example

```bash
MIX_ENV=test CONTRACT_LAB_DEMO=1 mix run examples/room_recovery.exs tmp/my-recovery-run
```

Use a fresh output directory. [The example](examples/room_recovery.exs) bridges actual room-simulator notifications through wire normalization, trusted sender attribution, contract admission, and the replacement controller. Outgoing intents pass contract admission before the room post. The room transport cannot promise idempotent posting, so its adapter explicitly declares `idempotent: false`.

Inputs `1` and `2` produce totals `1` and `3` without calling the sidecar. Input `13` crashes the initial program. The scripted generator writes a fresh program artifact, the validator checks it, and the controller verifies prior behavior before resuming the failed input. The room receives total `16` once. A duplicated notification makes no second contribution. The controller then restarts from snapshot plus one completed input in the journal, preserving `16` and publishing no old replies. The room and contract authority remain running during this participant-controller restart; this is not a whole-host durable authority demonstration.

The example also accepts a typed `activate` room event from the configured requester, rejects it from the counter, and ignores duplicate control delivery. Explicit activation makes a separate sidecar call. Inspect [the recorded run](evidence/room-recovery-demo.log) or the generated journal and release files in the chosen directory. The normal path uses no model; the demonstrated repair provider is scripted.

## Author contracts from history and iterate

The [master skill](../skills/multi-agent-contracts/SKILL.md) routes to [creation](../skills/create-multi-agent-contract/SKILL.md), [iteration](../skills/iterate-multi-agent-contract/SKILL.md), and [program replacement](../skills/replace-agent-with-program/SKILL.md). It uses this compiler and runtime to check authored results.

The worked creation example includes [source chat](histories/review-chat.md), a [source-to-rule derivation](histories/review-derivation.md), the [resulting contract](contracts/history-review.band), and [asserted scenarios](scenarios). The [iteration request](histories/review-followup.md) yields a second contract while preserving the first; the idle policy it asked to extend no longer exists, so both keep the same thirty-minute contract clock. These conversations are clearly labeled synthetic. The replacement architecture comes from a real internal design discussion, which is not published.

```bash
mix contract.check contracts/*.band
mix contract.query contracts/assignment.band --role worker
mix contract.query contracts/assignment.band --type offer
mix contract.replay contracts/history-review.band scenarios/history-review-success.json
mix contract.replay contracts/history-review-v2.band scenarios/history-review-v2-deadline.json
```

`check` compiles every supplied path. Each result lists `admission_limits`: for every rule that enters a state with a timed duty, the last time the entry is still admissible before the contract cap (`last_admissible`, cap minus the longest new window). After that time the engine refuses it with `insufficient_time`. `query` returns full documented JSON or one selected role/type/state/outcome/scope. `replay` executes a JSON scenario with explicit actor bindings, session clock, message cap, and an assertion on every step. It runs in the local unchecked room-policy mode and prints a warning saying so. An assertion mismatch identifies the step and returns a nonzero command exit. Negative scenarios pass when the runtime rejects the prohibited action as expected.

A failed command prints one JSON object on stderr, `{"error": {"code": ...}}`, and exits with status 1, so callers branch on `code`. A compiler error keeps the specified body `line` and its `detail`, and adds the `path` and a `file_line` counted from the top of the file. A failed replay assertion reports its mismatch code with the step in `detail`.

`mix contract.replay --observe CONTRACT SCENARIO.json` instead records each decision
in the JSON result's `steps` array without requiring or checking `expect`. Rejected
messages and mismatched assertions do not stop observation. Invalid scenario
configuration or step shapes still fail. Observation retains the unchecked warning
and is not evidence of hosted authorization. Asserted replay remains the default.

The skill helper resolves its bundled runtime from its source location and works through installed symlinks. It supports these same commands from another current directory. See [the authoring reference](../skills/multi-agent-contracts/references/authoring.md) for the scenario format and supported syntax.

## Checks beside a remote agent

`ContractLab.AgentGate.project(state, actor, now)` exports a participant view and an opaque protocol checkpoint from the room authority. The checkpoint preserves the state, time, reference identities and kinds, counters, and budgets needed for admission. It removes message payloads from checkpoint facts and receipts. The separate documented participant view includes only the message facts visible to that actor.

`AgentGate.check(contract, projection, message, actor, now)` runs the same engine against that checkpoint and discards the tentative next state. It requires an explicit expected version and checks identity, contract/source binding, message schema, references, current turn, timers and quotas. A local rejection can wake the LLM before publication. A local acceptance still requires room admission: another participant can change the authoritative version before a proposed message arrives.

Projections must come from an authenticated room adapter. Their checksum detects corruption; it does not establish room authenticity. The `band-contract/agent-view/1` projection contains a bounded, portable JSON checkpoint (`band-contract/state/1`) with a canonical SHA-256 checksum. Keep protocol checkpoints outside the program and LLM prompt; provide the documented participant view instead. Private room checkpoints use `band-contract/room-checkpoint/1` and retain all payloads and receipts. Fixed-key decoding validates types, identities and state relationships without converting arbitrary input to atoms. Old uncompressed `contract-lab/*/1` checkpoints remain readable for upgrade recovery; new writers never emit Erlang terms. [Portability checks](../agent-runtime/evidence/production-portability-checks.json) cover recovery and rejection; [Python/Elixir checksum evidence](../agent-runtime/evidence/production-portability-cross-language.json) verifies the same control-character and Unicode data in both languages.

[The JSON-lines adapter](examples/engine_endpoint.exs) lets the Python remote host run a separate local checker process. Its pipe also exposes authority operations for the reference room test harness. That pipe is trusted host control and must not be exposed as a public endpoint. The [Python runtime](../agent-runtime/README.md) owns agent-side storage and handoff; the room remains a separate authority.
