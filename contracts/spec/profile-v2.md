# Reviewed contract profile: 0.2.0-lab.1

This is the base profile for **new multi-agent skills**, implemented in the local
reference engine. Existing sessions MUST NOT be reinterpreted or upgraded in
place. Band has not yet been qualified for this profile.

Begin with [one review, explained from scratch](start-here-v2.md). Then read
the complete [agent-to-agent example](../contract-lab/contracts/v2/assistant-answer.band)
and [presence example](../contract-lab/contracts/v2/presence-recovery.band), then:

1. [Exact file identity](file-identity.md): raw UTF-8 bytes, SHA-256, optional author version and immutable session pinning.
2. [Recovery defaults](defaults-v2.md): small discovery header, ordinary body keywords, a mandatory contract clock (`contract @infinity => expired` opts out), no idle timer, finite owed-work duties and completion admission.
3. [Lifecycle](lifecycle-v2.md): `new` is entry-only; compact timed replies; optional message bindings and references.
4. [Text](text-v2.md): mandatory byte bounds, optional grapheme bounds and reserved UTF-8 encoding.
5. [Safe patterns](safe-patterns.md): regular-expression literals use `~r`; bounded deterministic automata; compilation and matching limits. Quoted pattern literals were removed.
6. [Agents and extensions](agents-and-extensions.md): humans are exempt, reusable role names, fixed room assignments, declared versioned capabilities and current trusted permissions.
7. [Host signals](host-signals.md): immediate/dwell transitions, watched humans, immediate agent removal, trusted ordering, bounded controls and durable recovery.
8. [Two-reviewer walkthrough](groups-and-events-v2.md): first reply versus both replies, deadlines, progress updates and optional room notices.
9. [Literal sigils](sigils-v2.md): raw `~s` documentation and `~w` lists for enums, references and role groups.
10. [Timeout scopes](timeouts-v2.md): `review() @5m => collected | stalled`, state caps and whole-run caps; opt-in `band-contract/timeout/1` compiled data.

11. [Lowercase keywords](lowercase-v2.md): the only body spelling; uppercase aliases and plus-prefixed declarations were removed.
12. [Room and client separation](room-clients-v2.md): mandatory room enforcement and discovery; optional experimental client machinery.
13. [Prompt metadata](multi-agent-skills.md#guidance-is-part-of-the-skill): ordinary quoted strings attach agent guidance to the next item; use sigils only when needed.
14. [Contribution outcomes](contribution-outcomes.md): a member stops its own contribution or the host reports a terminal failure with its original cause; recoverable errors stay pending; no `unable()` message.

The base parser, typed messages, accepted facts, reference checks, projections,
idempotent receipts, external execution and durable-host responsibilities remain
as described in the [contract DSL](contract-dsl.md) and [engine](engine.md)
documents except where these documents explicitly change them. `compiler/2`
emits engine `0.2.0-lab.1`; `session-engine/2` persists `band-contract/state/2`,
or state/3 to state/5 when the opt-in [coordination features](#coordination-additions)
are used. A checkpoint or projection reader that does not explicitly support a
state profile MUST fail closed.

## Machine interface and checks

The [schema bundle](schemas/protocol-v2.schema.json) describes portable source,
compiled contract, new session configuration, projections, checkpoints, trusted
room policy and host controls. [Requirements](requirements-v2.json) identify the
new obligations. Structural schema checks do not replace semantic validation,
UTF-8 byte limits, authority checks or runtime evidence.

The JSON-lines **trusted host pipe** supports `host_signal` in addition to the
existing compiler/session operations. `new.config` accepts negotiated
`capabilities`, `max_controls`, immutable `watch_bindings`, and trusted
`room_policy`; `step` receives fresh `room_policy` separately from its message.
These fields MUST NOT be copied from public agent request bodies as authority.
A Band API must derive them from verified current host state. Its existing public
wire binding is not evidence that these additions are integrated.

Run from the repository root after compiling the development reference engine:

```sh
(cd contract-lab && mix compile --warnings-as-errors)
.venv/bin/python -m contracts.spec.tools.checkv2
.venv/bin/python -m contracts.spec.tools.checkspec
```

The first script validates the new generated schema and explicit expected
behavior across exact identity, defaults, ordinary replies, entry-only state,
patterns, capacity, presence, checkpoint restart and removal. The second keeps
the original 78 cases / 283 requests and 33 Band wire examples unchanged. These
checks plus focused RED/GREEN unit evidence establish local reference behavior;
they do not qualify another language, Band deployment or an external-effect
recovery guarantee.

## Review expectation ledger

Consumer: **Eric**, reviewing this specification and its narrated presentation.
No downstream developer has accepted a Band rollout handoff.

| Requested item | Disposition and evidence |
|---|---|
| Header format chosen after the spikes | Changed: YAML frontmatter is the discovery header and every example uses it; standard skill packages carry the same fields ([contract DSL](contract-dsl.md)) |
| Recovery-first defaults and simpler discovery/body | Changed: reference implemented; no idle timer and no implicit cap; ordinary keywords; completion-capacity checks |
| Mandatory contract expiry | Changed: every contract body declares `contract @window [max …] => outcome` or fails with `contract_clock_required`; `contract @infinity => expired` is the explicit opt-out |
| Mention limits per message, not per session | Changed: `mentions N` (or `mention N`) on the `contract` line sets a default cap and on a rule overrides it; no cap unless set; no session budget; `mention_limit` at compile time, session creation and admission ([contract DSL](contract-dsl.md#mention-caps)) |
| Remove superseded forms | Changed: `due … with … in`, uppercase aliases, `+` declarations, `~S`/`~R`/`~W`, quoted regexes, `activity`, `inactivity-timeout`, `role x: list(y)`, builtin `list(agent)`, collect `failure type()` and the native directive header are rejected; a timed event such as `event a -> b: k() @5m => stalled` replaces an event-satisfied duty ([contract DSL](contract-dsl.md#lexical-rules-and-grammar)) |
| Optional author version; exact entire-file SHA; no in-place upgrade | Changed: reference implemented and restore mismatch tested |
| Mandatory byte bound; optional graphemes; UTF-8 reservation | Changed: compiler and payload admission implemented and tested |
| Simple one-role duty; optional correlation | Changed: a request that waits for its answer is a reply head, `requester -> assistant: question() awaits answering` with `=> answer() & complete` and `\| @5m & stalled`; the compiler supplies the binding and reference, so the simple example writes no `as`/`=$` ([lifecycle](lifecycle-v2.md)) |
| Every request declares its reply types and transitions | Changed: [reply alternatives](contract-dsl.md#reply-alternatives) are the only way to wait for an answer; a send answered by a separate rule in its target state is refused with `reply_required`; group requests wait for the group-head design |
| Entry-only `new` | Changed: all target forms rejected at compile time |
| Regex only with bounded runtime | Changed: restricted DFA matcher with compile/work limits; no authored backtracking engine |
| Regex literals without JSON escaping | Changed: raw `~r/.../`, with other delimiters also accepted; compiler, literal regressions and portable adapter checks implemented; quoted patterns removed |
| Use sigils beyond regex | Changed: `~s` raw documentation and `~w` name lists for enums, references and role groups implemented; examples, metadata, uppercase-alias rejection and group runtime checks pass |
| Explain group rules, duties, events and room scope | Changed: worked two-reviewer example, explicit success/timeout rules and traces; event and room scope explained separately; engine semantics unchanged |
| Make the explanation accessible without sacrificing depth | Changed: plain-language entry guide, one complete review example and a minimal-syntax policy; the narrated presentation (three core parts plus an optional fourth on the runner experiment) has a separate delivery record |
| Compact message, state and whole-contract timeouts | Changed: three compiler/runtime features implemented with observed RED/GREEN; versioned scope-clock data, restart, group, capacity and boundary checks; [specification and tests](timeouts-v2.md) |
| Lowercase keywords and presentation styling | Changed: lowercase keywords and sigils are the only spelling; uppercase aliases and plus-prefixed declarations are rejected |
| Ordinary agents and optional client experiments | Changed: core/optional boundary and discovery requirements explicit; direct-client example passes; public Band catalog and rejection guidance need integration |
| Human and agent-human exemption | Changed: normative scope and reference routing/binding checks; Band routes still need alignment |
| Fixed room role assignments | Changed: host policy enforces protected agent identity outside contract source |
| Versioned extension declaration and permissions | Changed: opt-in registry, capability negotiation and fresh trusted policy checks |
| Immediate/dwell presence and participant removal | Changed: reference implemented with restart, boundary and capacity evidence |
| New narrated presentation | Changed: rebuilt from this profile; live publication recorded with the delivered artifact |
| Retained prompting and multi-agent skill framing | Changed: documented roles, states, messages and outcomes; multiline author comments remain separate |
| Runtime participant lists | Changed: reusable participant types, validated attributes and agent views implemented; [current guidance](multi-agent-skills.md) teaches the agreed design |
| Partial results and early offline completion | Changed: collection result selected by Eric, per-member statuses, declared grace, trusted prior presence and active continuation implemented |
| Explicit heartbeat targets and maximums | Changed: adjacent `@self`, `@state`, `@contract`, immutable maxima and explicit infinity implemented with pinned feature profiles |
| Replace `failure unable()` | Changed: `room.contributions/1` member-stop and trusted host-failure controls, scoped to the collection round, keep the original cause; recoverable errors stay pending; collect `failure type()` was removed; [contribution outcomes](contribution-outcomes.md) |
| Band integration and second-platform interoperability | Not yet: requirements and acceptance matrix explicit; no merge, deployment or live qualification claimed |

Source is maintained in a local-only repository with no configured Git remote.
Each behavioral feature has its own commit and observed failing then passing
checks. Machine-generated behavior must still be reviewed and qualified by the
host implementer before rollout.

## Coordination additions

[Multi-agent skills](multi-agent-skills.md) combine retained prompting with the contract.
[Collections](coordination.md) bind runtime lists and retain partial results, including
trusted offline grace. [Renewals](renewable-timeouts.md) target exact declarations
and preserve immutable maximums. [Contribution outcomes](contribution-outcomes.md)
settle a member as `failed` through room controls (member stop or trusted host
failure) instead of a business message. These opt-in features use the separate
[coordination profile](coordination-profile.md); earlier source semantics stay pinned.
Ordinary quotes and simple lists are the authoring defaults. When a sigil is
needed, write it in lowercase.
