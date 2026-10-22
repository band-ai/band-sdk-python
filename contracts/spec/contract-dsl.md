# Contract DSL specification

This document defines profile `compiler/2` for engine identifier `0.2.0-lab.1`, the profile for new multi-agent skills. The [engine specification](engine.md) gives runtime meaning, and the [reviewed profile](profile-v2.md) indexes the extensions summarized here. A compiler MUST treat contract files as data. It MUST NOT execute expressions, interpolation, shell commands, import directives, or instructions contained in documentation. Parsing malformed documentation MUST NOT intern attacker-selected names into permanent runtime atom/symbol tables, including when the final parse result is rejected.

## Package and discovery

Discovery MUST read at most 4,096 application bytes, including the terminating delimiter and its newline, without reading the body. A smaller positive host limit MAY be configured. The delimiter must be complete within the limit. EOF without it is an error. LF and CRLF header lines are supported; delimiter matching ignores the line's trailing CR but not surrounding spaces. Text must be valid UTF-8. A BOM is not part of a valid delimiter.

Contracts are written with YAML frontmatter, and every example in this specification uses it. A standard skill package carries the same fields as metadata. The caller selects the format explicitly (`frontmatter` or `standard`); no format is inferred from a filename. The native directive header is not accepted for this profile and fails with `frontmatter_required`.

| Frontmatter field | Standard metadata | Required | Value |
|---|---|---|---|
| `name` | Package `name` | Yes | 1–64 ASCII bytes matching `[a-z0-9]+(-[a-z0-9]+)*` |
| `description` | Package `description` | Yes | String whose trimmed length is 1–1,024 Unicode 16.0 extended grapheme clusters; original value is retained |
| `engine` | `band-engine` | Yes | Semantic version syntax; this compiler accepts `0.2.0-lab.1` |
| `roles` | `band-roles` | Yes | Nonempty, ordered, unique names using the same name syntax; YAML sequence or comma-separated string |
| `version` | `band-version` | No | Author's semantic-version label; see [file identity](file-identity.md) |
| `encoding` | `band-encoding` | No | Only `utf-8`, the default; see [text](text-v2.md) |
| `extensions` | `band-extensions` | No | Unique identifiers from `band.permissions/1`, `host.presence/1`, `host.membership/1` and `room.contributions/1` |

Unknown or duplicate fields are errors. Mention caps are not header fields either: `mentions-per-message` and `mentions-per-session` are rejected as unknown fields, and a cap is the body option `mentions N` ([mention caps](#mention-caps)). `inactivity-timeout` is also rejected as an unknown field, because this profile has no generic idle expiry ([defaults](defaults-v2.md)). The whole-run clock is not a header field: `max-duration` and `lifetime` are rejected as unknown fields, and the body declares the clock ([mandatory contract clock](#mandatory-contract-clock)). Safe integers are at most 9,007,199,254,740,991. Numeric string fields have no sign or leading zero. Duration units denote 1, 60, 3,600 and 86,400 seconds; session initialization checks all resulting millisecond deadlines for overflow. The [recovery defaults](defaults-v2.md) explain why no idle limit is implied.

```yaml
---
name: review
description: Review a submitted artifact and acknowledge completion.
engine: 0.2.0-lab.1
roles: [author, reviewer]
---
```

The body of this file appears under [lexical rules and grammar](#lexical-rules-and-grammar).

Frontmatter starts with `---` and ends with the next `---` line, both followed by a newline. It contains the fields above in a YAML mapping. The remaining bytes are the body. The YAML profile permits ordinary scalar, list, mapping, quoting and multiline-string syntax. It prohibits duplicate mapping keys at every depth, non-string mapping keys, anchors, aliases, explicit tags, tag directives, multiple documents, and values inconsistent with the field types. YAML coercion MUST NOT create executable objects or atom/symbol names.

Standard skill packages use YAML frontmatter with `name`, `description`, `metadata`, and optional `license`, `compatibility`, `allowed-tools`. The containing directory's basename MUST equal `name`. `metadata` is a string-to-string map. Fields other than name/description use the `band-` keys in the table. Two additional strings are required: `band-contract` and `band-contract-sha256`. Unknown `band-` keys are errors; unrelated metadata keys are allowed.

The contract reference is a relative `.band` path matching `[a-zA-Z0-9_/-]+\.band`, with no `.` or `..` component, absolute path or symlink component. Its SHA-256 is 64 lowercase hexadecimal characters over the exact body bytes. Standard `SKILL.md` prose after frontmatter is not the contract body. Loading MUST verify the unchanged discovery prefix, bounded body bytes, file identity/path policy and referenced digest against an immutable package snapshot. Discovery never reads referenced bodies.

Normalized cards use keys `name`, `description`, `engine`, `version`, `encoding`, `extensions`, `roles`, `lifetime` and `inactivity_timeout`, with defaults resolved; `lifetime` comes from the body clock and is `"infinite"` for the opt-out. A finite duration becomes `["seconds", n]`; infinite becomes `"infinite"`. Original role order is retained. The body is at most 262,144 bytes and valid UTF-8. A new session is created from the exact file, a `band-contract/file-source/1` source, rather than from a normalized card and body; see [file identity](file-identity.md).

The reference loader reads regular files, checks descriptor/path identity before and after reading, rejects a replaced discovery entry with `source_changed`, and rejects an oversized body with `source_size` before returning it. Header changes take precedence as `header_changed`. Inline loading reads no more than prefix length plus the body bound plus one overflow byte; referenced loading reads no more than the body bound plus one. Operators MUST select packages from directories that other principals cannot mutate, then pin the selected in-memory source identity. File metadata checks do not replace immutable deployment artifacts or the referenced content digest.

## Lexical rules and grammar

The body is line-oriented. Each physical line has a one-based line number, beginning immediately after the discovery delimiter (or at the first line of a referenced body). Outside documentation, trim Unicode whitespace at both ends. Empty lines, whole-line comments beginning `%%` and author notes are ignored. An author note opens with `/*` at the start of a line and closes with `*/` at the end of the same or a later line; notes do not nest, and nothing may follow the closing `*/`. Comments and notes are for authors; agent guidance belongs in [documentation](#attached-documentation). Inline comments are not supported.

Keywords are lowercase and apply only in their grammar positions: a role named `event` can still send a message. Uppercase keywords or rule options and plus-prefixed declarations are `syntax` errors whose detail names the lowercase form. Arbitrary mixed case such as `Each` is not supported. Names are case-sensitive.

`identifier` is `[a-z][a-z0-9_]*`. It names types, fields, states, outcomes and bindings. Explicit role selectors use this identifier syntax and must name a declared card role; `*` expands all card roles in card order, including card names that cannot be spelled as an explicit selector. Authors SHOULD use simple lowercase alphanumeric role names for maximum portability across these syntaxes. A later syntax extension must be versioned explicitly.

The body of the `review` file above exercises the core forms:

```band
"""
Review a submission until the author accepts it. The whole run, revisions
included, has one hour.
"""
contract @1h => expired

/* Author note: one reviewer keeps this example small. */
"""
You submit work and decide what happens after each review: accept it, or send
a revised submission that addresses the review.
"""
role author

"""
Inspect the submission and report evidence.
"""
role reviewer

"""
The work under review: a short title and the full body.
"""
type submission
  title: text(bytes: 256, graphemes: 80)
  body: text(1..20000)

"""
Your verdict on one submission. Say approve only when the evidence supports it,
and explain every revise in the notes.
"""
type review
  source: ref(submission)
  decision: enum(approve|revise)
  notes: text(4000)

"""
A short note that the review is under way, so the author knows it has not stalled.
"""
type progress
  note: text(1..200)

"""
The author accepts the submission as reviewed; the run ends.
"""
type accept

"""
Nothing has been submitted yet. The author sends the first submission.
"""
state new
  author -> reviewer: submission() as artifact awaits reviewing
    """
    Review the latest submission within thirty minutes.
    """
    => review(source=$artifact) & deciding
     | @30m & stalled

"""
A submission is under review. At most three review rounds are allowed.
"""
state reviewing max_entries 3 => exhausted
  event reviewer -> author: progress()

"""
The author has a review. Accept the submission, or revise it and submit again.
"""
state deciding
  author -> reviewer: accept() => accepted
  author -> reviewer: submission() as artifact => reviewing

end accepted

end stalled

end exhausted
```

The submission is a request with [reply alternatives](#reply-alternatives): it awaits `reviewing`, a review leads to `deciding`, and with no review within thirty minutes the run ends `stalled`. A revised submission from `deciding` is a re-send into `reviewing`, which allows three rounds.

The following EBNF describes trimmed logical lines. `SP` is one ASCII space where shown; rule options and arrow tails are tokenized by whitespace. Type colons permit surrounding whitespace, and `text(...)` permits whitespace after its commas and after `bytes:`, `graphemes:` and `regex:`. Comma-separated role sets do not contain spaces; a word list separates names with spaces or tabs. Comma-separated reference arguments may have surrounding whitespace. A line cannot contain two declarations.

```ebnf
body         = { blank | comment | note | documentation | declaration } ;
declaration  = contract | role | type | field | state | rule | timed-rule | timed-event
             | wait-head | collection | trigger | watch | permission
             | "anystate" | "room" | "end ", identifier ;
contract     = "contract", [" @", window, [" max ", ["@"], window], [mentions], " => ", identifier] ;
window       = duration | "infinity" ;
role         = "role ", card-role, [": list(", identifier, ")"]
             | card-role, ": list(", identifier, ")" ;
type         = "type ", identifier, [": agent"] ;
field        = identifier, ":", field-type ;
field-type   = "boolean"
             | "integer(", unsigned, "..", unsigned, ")"
             | "text(", ["bytes:"], range, [",", "graphemes:", range], [",", "regex:", pattern], ")"
             | ("enum" | "ref"), "(", (identifier, {"|", identifier} | word-list), ")" ;
range        = unsigned, ["..", unsigned] ;
pattern      = "~r", ("/", pattern-text, "/" | "|", pattern-text, "|" | "{", pattern-text, "}") ;
state        = "state ", identifier, [" @", duration, " => ", identifier],
               [" total @", duration, " => ", identifier],
               [" max_entries ", positive, " => ", identifier] ;
rule         = ["event "], route, [" => ", (identifier | reset)] ;
timed-rule   = route, " @", duration, " => ", identifier, " | ", identifier ;
timed-event  = "event ", route, " @", duration, " => ", identifier, [" @self"] ;
wait-head    = ["event "], route, " awaits ", (identifier | reset), {alternative}
             | ["event "], route, [" => ", (identifier | reset)], alternative, {alternative} ;
alternative  = newline, ("=> " | "| "), (reply-alt | timeout-alt) ;
reply-alt    = identifier, "(", [reply-arg, {",", reply-arg}], ")", [" as ", identifier],
               " & ", identifier ;
reply-arg    = argument | identifier, "=", identifier ;
timeout-alt  = "@", duration, " & ", identifier ;
route        = selector, " -> ", selector, ": ", call, {option} ;
call         = identifier, "(", [argument, {",", argument}], ")" ;
argument     = identifier, "=$", identifier ;
option       = " as ", identifier | " each" | mentions ;
mentions     = (" mentions " | " mention "), (positive | "infinity") ;
reset        = "@self" | "@state" | "@contract" ;
selector     = "*" | identifier | "{", identifier, {",", identifier}, "}" | word-list ;
word-list    = "~w", delimited-names ;
collection   = "collect ", identifier, " from ", selector, " -> ", selector,
               " @", duration, " => ", identifier, collect-line, {collect-line} ;
collect-line = newline, ("reply ", call | "offline ", ["@"], duration
                         | "quorum ", ("all" | identifier), " => ", identifier) ;
trigger      = "on ", ["watch."], identifier, " ",
               (("online" | "offline"), [" for ", duration] | "removed"), " => ", identifier ;
watch        = "watch ", identifier ;
permission   = "require ", card-role, " band.permission ", ("owner" | "admin" | "participant") ;
duration     = positive, ("s" | "m" | "h" | "d") ;
```

A `wait-head` is a send followed directly by its alternatives ([reply alternatives](#reply-alternatives)). Documentation and ignored lines may come between them, and a `"""` prompt documents the alternative below it. The first alternative starts with `=>` and each later one with `|`; no other line starts with either. The formatter writes `=>` two spaces deeper than the head and `|` one space deeper again, so the alternatives line up, and writes a documentation, comment or note line before an alternative at that alternative's indent (4 before `=>` and 5 before `|` under a state rule). In a `reply-arg`, `field=$binding` is a reference and `field=value` a literal. The grammar accepts any head, `awaits` followed by a name or a renewal, an `awaits` with no alternatives, and any mix of alternatives, so a block of the wrong shape is reported with its `reply_*` code rather than `syntax`. Each alternative is one line: two alternatives on one line are a `syntax` error. `card-role` is a name from the card's `roles`. Patterns are written `~r/.../` as in the presentation; the `|` and `{` delimiters are also accepted. `pattern-text` is restricted pattern source, and `delimited-names` is a delimited literal of names such as `[a b]`; the linked documents define both. `blank`, `comment`, `note` and `documentation` are described in the surrounding sections.

Field bounds are nonnegative safe integers with `min <= max`; a single `text` bound is a maximum with minimum zero. Unlike header numeric strings, bound and entry-limit digit sequences may include leading zeroes; their normalized values are integers. Every `text` field has a UTF-8 byte bound, and `graphemes` and `regex` add constraints that must also pass ([text](text-v2.md), [safe patterns](safe-patterns.md)). Enum/ref lists are nonempty, contain valid unique identifiers and preserve source order; a `~w` word list is also accepted ([sigils](sigils-v2.md)). Durations are strictly positive, have no leading zero, and must fit safe milliseconds. An entry limit (`max_entries N`) is a positive safe integer; zero or a value above 9007199254740991 fails with `rounds`.

`type` starts a field section; `type name: agent` declares a participant type whose fields cannot be references. `state` starts a section for rules, timed rules, timed events, collections and triggers; `anystate` starts global session rules and triggers; `room` starts sessionless rules. `contract`, `role` and `end` close the current section, and `watch` and `require` appear only while no section is open. Fields outside a type, timed events and collections outside a state, triggers outside a state or `anystate`, and rules outside state/anystate/room sections are errors. The `contract` line is required, occurs once, carries the whole-run clock ([mandatory contract clock](#mandatory-contract-clock)) and is the contract's metadata attachment point. `role` is an optional metadata attachment point and occurs at most once per role. The initial state `new` is required. `expired` exists without a declaration, as do the reserved outcomes `participant_removed`, `control_capacity_exhausted`, `insufficient_time`, `completion_capacity` and `unserviceable_obligation`; `end expired` only attaches documentation.

An ordinary rule requires a target state or outcome, and no rule, deadline, cap or trigger may target `new` ([lifecycle](lifecycle-v2.md)). An `event` has no target and never transitions; in a named state it may instead renew `@self`, `@state` or `@contract` ([renewal](renewable-timeouts.md)). `each` is forbidden on untimed events and cannot be combined with `as`. `as` binds the accepted message's ID, not its payload. Options may appear in any order but each may occur at most once. A `room` rule must be an event with no `as` or reference matches.

A timed rule `review() @5m => collected | stalled` is a state-local transition plus a duty that starts on state entry: with `each` or a single sender every selected sender owes the message, and a group without `each` needs one. A timed event `event worker -> lead: ack() @5m => stalled` is an owed message that does not move the state: it is allowed only directly in a `state`, compiles to a duty that starts on state entry, and an accepted event discharges that duty. The event itself never transitions. The duty is owed once per entry into the state: one accepted event discharges it, it does not re-arm, and it is not a recurring heartbeat. Repeats are accepted but owe nothing. A timed event may also end with `@self`, as in `event worker -> lead: progress() @15m => unacknowledged @self`: each accepted event then discharges its own duty (the first time) and renews the immediately following timed rule or `collect`, exactly as a separate `=> @self` event would ([renewal](renewable-timeouts.md)). Only `@self` combines with a timed event; `@state` and `@contract` do not. Its single outcome MUST be an `end` terminal, its duration is finite and positive, and it has one deadline and one outcome. With `each`, every selected sender owes the event; a single sender that is not a roster owes it; a group or roster without `each` needs one sender. `state name @5m => outcome` caps one state entry, `total @2h => outcome` on the same line caps the state's time over all its entries ([cumulative budget](timeouts-v2.md#a-cumulative-state-budget)), and `contract @30m => expired` caps the run from creation; `max` makes the contract window renewable up to that maximum ([timeout scopes](timeouts-v2.md)). `collect`, `list(...)` roles and participant types are opt-in [collection](coordination.md) features. `watch`, `on` and `require` need the declared [host extensions](host-signals.md).

## Reply alternatives

A send can list the replies it waits for, and each reply leads to its own next state. The form is UBF's: a request `=> reply & next state`, with `|` before each further alternative. Here the next state is worked out from the reply itself, and nobody supplies it. The alternatives must cover every reply the recipient could validly send, so a contract cannot force one answer, and the last alternative is the timeout.

```band
"""
Deploy one build. The deployer answers each deploy request with one reply, and
that reply decides what happens next.
"""
contract @2h => expired

"""
You request the deploy and react to its outcome: send it again after a
retryable error, or abandon the job.
"""
role lead

"""
Deploy the requested build and reply to that request with exactly one outcome.
"""
role deployer

"""
The build to deploy.
"""
type deploy
  build: text(1..200)

"""
The deploy succeeded.
"""
type ok
  request: ref(deploy)

"""
The deploy failed. Set retryable to true only when sending the same build again
could succeed.
"""
type error
  request: ref(deploy)
  retryable: boolean

"""
A short note that the deploy is still running.
"""
type progress
  note: text(1..200)

"""
The lead gives up on the deploy.
"""
type abandon

"""
Nothing is deployed yet. The lead sends the first deploy request.
"""
state new
  lead -> deployer: deploy() awaits deploying
    => ok() & deployed
     | error(retryable=true) & retrying
     | error(retryable=false) & failed
     | @10m & stalled

"""
The deployer is working on the latest request and owes one reply to it within
ten minutes. At most three deploy attempts are allowed.
"""
state deploying max_entries 3 => exhausted
  event deployer -> lead: progress() => @self

"""
The last deploy failed but can be retried. Send it again, or abandon the job.
"""
state retrying
  lead -> deployer: deploy() => deploying
  lead -> deployer: abandon() => failed

end deployed

end failed

end stalled

end exhausted
```

Read the first deploy as: **the lead sends a deploy and awaits it in `deploying`. An `ok` moves the job to `deployed`, a retryable `error` to `retrying` and any other `error` to `failed`. With no reply by ten minutes the job ends `stalled`.**

- **Head and wait state.** A send followed by alternatives is a *head*. `awaits W` names its wait state W. Without `awaits`, W is named after the head's kind: `lead -> reviewer: request()` awaits `request_wait`. A W that equals another state or terminal is a `duplicate`, reported at the later line, and so is a `state W` section for a W that no `awaits` names: a `state W` section requires `awaits`. A second head with alternatives for the same W, named or implied, is `reply_route`. The head has no `=> target` of its own: the alternatives are its targets, and `awaits` names a state, never a renewal (`awaits @self` is `reply_route`). W's documentation is the prompt of its `state W` section when it has one, otherwise the head's prompt; a W without a section is therefore documented, for `--require-prompts`, by documenting its head.
- **Alternatives.** The first alternative starts with `=>` and each later one with `|`, one per line. `R & S` reads "reply R leads to state S". `| @d & T` is the timeout: it comes last, exactly once, and T is an `end` terminal.
- **Two meanings of `|`.** In the middle of a timed rule, `|` separates the success target from the timeout target (`review() @5m => collected | stalled`). At the start of a line under a head, it opens one more alternative. No other line starts with `|`.
- **Literals.** In `error(retryable=true)`, `field=value` names an enum member, or `true` or `false` for a boolean field. Text, integer and ref fields cannot take literals. `field=$binding` keeps its usual meaning.
- **Re-send.** Another rule may enter W only as a re-send: an ordinary rule `… => W` with the declaring head's route, kind and binding (written or implied), and no alternatives of its own. It reuses W's block. This is how a loop returns to W, as `retrying` does above. A re-send is never written with `awaits`; an `awaits W` with no alternatives is `reply_timeout`. A rule into W with a different route or kind is not a re-send (`reply_route`), and a re-send whose binding differs from the head's is `reply_correlation`, reported at the re-send.

  A re-send without `as` binds W's name, exactly as a head without `as` does. So when the declaring head writes `as job`, every re-send must write `as job` too; a bare re-send binds `deploying` instead and fails closed with `reply_correlation`:

  - declaring head, with its alternatives below it: `lead -> deployer: deploy() as job awaits deploying`
  - re-send, with the same binding: `lead -> deployer: deploy() as job => deploying`

  Without `as` on the head, as in the example above, the re-sends need none either: every head binds `deploying`.
- **The `state W` section** is optional. It may hold events, timed events, untimed rules from the requester (such as a cancel), `on` triggers, a state clock, `total` and `max_entries`. It may not hold timed rules, `collect`, or rules from the replier that move the state.
- **Correlation is always checked.** A reply answers a request only because it names that request's ID. The engine never infers which request a reply answers from the shape of the contract: not from W being entered once, nor from a reply arriving first. Every block therefore has, or fails with `reply_correlation`:
  - one binding for every head that enters W, re-sends included;
  - on every alternative, a `ref` field whose kinds include the head's kind, matched `=$` to that binding;
  - no rule or event that applies while in W (in `state W` or `anystate`) binding that name, so it cannot change during the wait.

  A late reply to an earlier request then fails the [`reference` check](engine.md#admission-algorithm-and-precedence), whatever path led back to W. A message kind used as an alternative is also reply-only for its sender role: an ordinary rule or event that accepts the same (role, kind) outside a reply block is a `reply_correlation` error.
- **Correlation sugar.** The example writes neither binding nor reference; the compiler supplies both, and checks them exactly as if written:
  - A head without `as` binds W's name, so the heads above bind `deploying`.
  - An alternative that writes no `=$` for its correlation gets `field=$<binding>` on the single `ref` field of its type whose kinds include the head's kind, here `request=$deploying`. With no such field, or several, the alternative is a `reply_correlation` error until the author writes the reference.
  - The explicit form stays legal: `deploy() as job awaits deploying` with `=> ok(request=$job) & deployed`. The formatter never adds or removes the sugar.
- **Cycles.** If W lies on a cycle (it can reach itself through any rule, trigger or deadline), W also declares `max_entries`, which bounds how often the replier can steer the run back into it.
- **Bindings.** An alternative's `as` (`ok() as done & deployed`) binds the reply's ID. All alternatives in a block bind the same name or none, and that name is not the head's.
- **One sender, one recipient.** A head is not an event. It has one sender role and one recipient role, with no `each`, roster or group. A fan-out stays a [collection](coordination.md), which branches with `quorum`.
- **Authority.** The outcome a reply selects is the replier's assertion. A host MUST NOT treat a terminal such as `approved` as authorization beyond what it already trusts the replier's role to decide.

**A request that waits for an answer must be a reply block.** When a send expects an answer, and that answer decides what happens next, write the send as a head with alternatives. A send into W followed by a separate answer rule in W is the compile error `reply_required`.

- **Wrong.** `lead -> reviewer: request() => reviewing` in `state new`, then `reviewer -> lead: review() => complete` in `state reviewing`. The compiler refuses the pair at the request's line.
- **Right.** One block in `state new`: the head `lead -> reviewer: request() awaits reviewing`, then the alternative `=> review() & complete` on the next line and the timeout `| @25m & stalled` below it, laid out like the submission in the review contract above.

Take the timeout from the old answer's timer or from W's state clock. When the answer had neither, choose one that fits inside the state or contract clock. The rule in detail:

- A *request* is an ordinary send in a `state` or `anystate` section, from one sender role A to exactly one recipient role B, whose target W is a state rather than an `end` (W may be the send's own state). Events, heads, alternatives and re-sends are not requests, and neither is a send to a group, a roster or `each`; group requests have no reply form yet.
- An *answer* is a rule in W's own `state W` section that is not an event, is sent by B, includes A among its recipients and moves the state, timed or not. An `anystate` rule is never an answer.
- A request with an answer is an error, except when the request is timed and all of its answers are untimed. A timed send discharges a duty, so it is an owed report, and B's moves after it are new decisions: `ack() @15m => working` followed by the lead's `revise`, or `deliverable() @1h => delivered` followed by `accept`, `drop` or `revise`, compile as written.
- With several such requests, the error is reported at the earliest. Its `detail` names the answer's line and the head to write.
- The rule is a structural guard, not a reading of intent. An answer written in `anystate` is not caught. A one-way untimed send followed by an unrelated move back from B is caught; writing it as a reply block is the intended fix.

Reply alternatives are core syntax: authors do not declare an extension for them. When a contract has a reply block, the compiler adds `contract.replies/1` to the compiled card's `extensions` list, so a host or engine that does not support it refuses the contract rather than ignore the alternatives. Authors never declare it, and the header refuses it. A contract without reply blocks compiles exactly as before. [Engine admission](engine.md#admission-algorithm-and-precedence) picks the alternative, the [defaults](defaults-v2.md#reserve-known-response-capacity) reserve its capacity, and [timeout scopes](timeouts-v2.md#a-reply-wait) and [renewal](renewable-timeouts.md#renew-a-reply-wait) define the wait's clock.

## Mention caps

A mention cap limits how many principals one message may reach. `mentions N` after the clock on the `contract` line sets the default for every rule, for example `contract @1h mentions 8 => expired`. The same option on a rule overrides that default for the rule, and `mentions infinity` lifts it. `mention N` is the same option, so examples follow English agreement: `mention 1`, `mentions 8`. When neither the rule nor the `contract` line sets a cap, a rule may reach every principal it names. There is no session-wide mention budget.

A message counts its delivered principals: the rule's recipients, with rosters expanded to their bound members, duplicates removed and the sender excluded. The compiler checks every rule with fixed recipients and rejects one whose widest delivery exceeds its cap with `mention_limit` at the rule's line. A rule that names a roster is checked when a session is created ([engine](engine.md#session-configuration-and-trusted-context)), and admission checks each message again.

## Mandatory contract clock

Every contract declares its whole-run clock with exactly one body line,
`contract @window [max ceiling] => outcome`:

| Declaration | Meaning |
|---|---|
| `contract @30m => expired` | Fixed cap, measured from session creation |
| `contract @80m max @4h => expired` | Renewable window under a fixed ceiling ([renewal](renewable-timeouts.md)) |
| `contract @infinity => expired` | Explicit opt-out: no whole-run clock, and the session's `max_at` is null |

A source without this line fails with `contract_clock_required` at line 1. The
check runs after every other static check, so another error in the same source
is reported first. A bare `contract` line that only carries documentation does
not declare a clock, and the removed header fields `max-duration` and `lifetime`
cannot supply one. [Timeout scopes](timeouts-v2.md) define when the clock starts
and how it ties with other timers.

## Attached documentation

Documentation attaches to the next declaration, field, rule or collection; ignored blank, comment and note lines may intervene. The canonical prompt form is a block, and it is what the [formatter](format.md#prompts) writes: a line whose trimmed contents are exactly `"""` opens a multiline literal, and the next such line closes it. The literal's text ends at its last content line, since the line break before the closing delimiter is layout. A one-line ordinary string `"..."` is still accepted and decodes identically, so `"""`/`x`/`"""` and `"x"` document alike. Raw `~s|...|` text keeps backslashes and `#{` exactly as written; other delimiter pairs and a multiline raw block are also accepted ([sigils](sigils-v2.md)). Documentation is metadata and is never executable. Two pending literals, a pending literal at EOF, an unterminated literal, interpolation or a non-string decoded literal are errors. A literal cannot be closed and followed by a declaration on the same line.

The opening delimiter's newline is omitted. The closing delimiter's indentation is removed from each content line, up to the available leading indentation; relative extra indentation is retained. The line break before the closing delimiter is not part of the text. Empty content produces an empty string. Spaces and tabs are literal indentation characters, not a tab-width calculation. CRLF between content lines is retained as CRLF, and a final CRLF is dropped like a final LF; source identity hashes the original bytes. Implementations MUST pass the supplied literal fixtures rather than substituting generic Markdown dedenting.

Ordinary strings and multiline literals follow the literal compatibility profile for backslash escapes: `\0`, `\a`, `\b`, `\d`, `\e`, `\f`, `\n`, `\r`, `\s`, `\t`, `\v` represent NUL, BEL, BS, DEL, ESC, FF, LF, CR, space, TAB and VT; `\\`, `\"`, and `\#` quote their following character. `\xHH` inserts a byte; `\uHHHH` and `\u{H...}` insert a Unicode scalar. `\x{H...}` also has the Unicode-scalar meaning. A backslash immediately before a physical newline joins lines. Other escaped ordinary characters lose the backslash. Invalid hex/scalars and decoded invalid UTF-8 are errors. An unescaped `#{` is prohibited; it is never evaluated. Raw `~s` content is not unescaped. The full fixture set fixes edge cases, including escaped interpolation, quotes, indentation, continuation and final newlines.

Every attachable node has `meta: {doc, line}`. `doc` is the decoded string or null. An attached literal sets `line` to its opening line; otherwise `line` is the node's declaration line. Default contract/role/any/room metadata has null documentation at line 1. Full and role-specific query APIs MUST preserve this metadata, including field and deadline documentation. Documentation does not decide admission or certify semantic answer quality.

## Static validation

A compiler MUST reject, before a session is created:

- Unknown syntax, unsupported engine versions, malformed UTF-8, oversized source, unterminated or nested author notes, and duplicate declarations/fields/selectors/options.
- Missing `new`; overlapping state/outcome names; unknown role/type/state/outcome references; and any transition, deadline, cap, collection or trigger that targets `new`.
- Text fields without a byte bound, invalid field bounds, duplicate enum/ref members, unknown types named by a ref, invalid or oversized patterns, zero/overflowing durations and zero entry limits.
- A rule whose reference field lacks an exact `field=$binding` argument; an argument that is not a ref field; or a binding name never declared by an `as` rule anywhere in the contract.
- An `event` with a state transition; a timed event in `anystate` or `room`, with an outcome that is not an `end` terminal, or with `@infinity`; an untimed `event` with `each`; an ordinary rule without a target, forbidden `each`/`as` combinations, a renewal target outside one named state, or forbidden room references/bindings.
- A deadline, state cap, state total, whole-run cap or entry-cap outcome that is not terminal, a whole-run cap shorter than a response window in `new`, or a state total shorter than that state's per-entry clock or a declared response window.
- A collection `quorum` whose target is unknown or `new`, or whose attribute is not a `boolean` attribute of every member's participant type (`type`, reported at the `collect` line).
- A duty for a participant with no outgoing rule of the required kind in that state or `anystate`.
- A removed form: `due … with … in` (including `all of` / `any of`), uppercase keywords or rule options, plus-prefixed declarations, `~S`/`~R`/`~W`, a quoted regex, the `activity` option, `role x: list(y)`, builtin `list(agent)`, or collect `failure type()`. Each is a `syntax` error whose detail names the replacement.
- A `watch`, presence or membership trigger, `require` or collection `offline` without its declared extension, or removal of a bound agent that does not end the session.
- More than one applicable rule for the same `(sender role, message kind)` in a state's local rules plus `anystate`, or within `room`. Different match arguments do not resolve this ambiguity, with one exception: the alternatives of one [reply block](#reply-alternatives) are told apart by their literals, which must not overlap (`reply_overlap`). An alternative and a `state W` or `anystate` rule or event of the same `(sender role, message kind)` are still `ambiguous_rule`.
- A reply block that breaks the [reply alternative](#reply-alternatives) rules, with the codes in the table below.
- A rule with fixed recipients whose widest delivery exceeds its [mention cap](#mention-caps) (`mention_limit`).
- A request written as a send and a separate answer instead of a [reply block](#reply-alternatives) (`reply_required`, after the other reply codes, at the earliest such request).
- A source without a `contract @window … => outcome` line (`contract_clock_required`, checked last).

| Code | Rejects |
|---|---|
| `reply_route` | A head that is an event, uses `each`, names a roster or more than one sender or recipient role, has a target or renewal (`=> S`, `=> @self`), awaits a renewal (`awaits @self`), or sits in `anystate` or `room`; a second head with alternatives for the same wait state, named or implied, reported at the later head; an entry into a wait state other than its heads, including a rule into W whose route or kind differs from the declaring head's; a forbidden line in the `state W` section. |
| `reply_match` | A literal on a field that is not enum or boolean, a value outside the field's domain, a repeated field, alternatives of one kind with different `=$` references, or alternatives with different `as` names. |
| `reply_overlap` | Two alternatives of one kind that some payload matches both, because they agree on every field they both name. Reported at the later line. |
| `reply_not_exhaustive` | Alternatives that leave some valid reply of a listed kind unmatched. Reported at the head. A bare `kind()` is legal only as the only alternative of its kind. |
| `reply_correlation` | Heads of one wait with different bindings (written `as`, or W's name when omitted), including a re-send whose binding differs from the declaring head's, reported at the re-send; an alternative without a `ref` field matched `=$` to that binding, written or supplied by the sugar, or whose ref kinds do not include the head's kind; an alternative that omits its reference when its type has no `ref` field to the head's kind, or several; a rule or event that applies in W and binds that name; an ordinary rule or event outside a reply block that accepts a (role, kind) used as an alternative. |
| `reply_cycle` | A wait state on a cycle without `max_entries`. |
| `reply_timeout` | A block that is not one or more reply alternatives closed by exactly one `@d & T` alternative: a missing, repeated or early timeout, or no reply alternative; or a block whose first alternative does not start with `=>` (a `|` with no `=>` opener), or a later one with `|` (two `=>` lines); or an `awaits W` with no alternatives. A missing timeout and an `awaits` with no alternatives report at the head. A timeout outcome that is not terminal fails the existing deadline check. |
| `reply_limit` | More than 16 alternatives in a block, more than 4 literals in one alternative, or more than 256 alternatives in the contract. |
| `reply_required` | A send from one role to one other role into a state W whose own `state W` section holds an answer: a rule from that recipient back to the sender that moves the state. The pair must be one reply block. A timed send whose answers are all untimed is exempt. Reported at the earliest such send; `detail` names the answer's line and the head to write. |

Overlap is checked pairwise, and coverage after it. Once the alternatives are disjoint, coverage is a count: for each kind, over the fields any of its alternatives names, the payloads each alternative matches (the product of the domain sizes of the fields it leaves open) must add up to the product of all those domain sizes, computed exactly. Coverage counts only payloads that pass the type and reference checks. Literal values are never interned as atoms or symbols.

Binding validation is structural, not a proof that every execution path binds the variable before use. Runtime exact-reference checks decide that. Deadline satisfiability is also structural; the compiler does not promise that a participant will respond or that payload construction is possible along every history.

Rules, derived duties, host triggers and collection rules share a counter starting at zero in physical declaration order. Each rule gets `rule-N` and each trigger `host-N`, then the counter increments. A timed rule or timed event takes `rule-N` and its derived duty `due-(N+1)`. A reply block's declaring head takes `rule-N`, its k alternatives `rule-(N+1)` to `rule-(N+k)`, and its timeout `due-(N+k+1)`; the next declaration is numbered from N+k+2, and a re-send is an ordinary rule. Other declarations and documentation do not consume a number. This ordering participates in normalized identities and deadline ties.

Compiler errors are `{code, line, detail}`. Required codes are `input`, `source_size`, `engine`, `syntax`, `comment`, `documentation`, `orphan_documentation`, `duplicate`, `unknown_role`, `unknown_type`, `unknown_state`, `unknown_binding`, `unmatched_reference`, `unsatisfiable_deadline`, `ambiguous_rule`, `type`, `duration`, `rounds`, `initial_state_target`, `pattern_syntax`, `pattern_limit`, `timer_target`, `extension_required`, `host_trigger_limit`, `membership_outcome`, `contract_clock_required`, `mention_limit`, `reply_route`, `reply_correlation`, `reply_match`, `reply_overlap`, `reply_not_exhaustive`, `reply_cycle`, `reply_timeout`, `reply_limit`, `reply_required`, `unknown_field`, `duplicate_field`, `missing_field`, `header` and `field_value`. `line` is a positive one-based body line. Header errors use the same shape. Their line is the line of the key's YAML node, counting the opening `---` as line 1 and every YAML line break (LF, CRLF or CR), so a key-like line inside a block scalar never counts: `unknown_field`, `duplicate_field` and `missing_field` name the field (a repeated field reports its first repeat within the same mapping, where parsing stops; a standard package's `band-<field>` key is used when there is no top-level key of that name; a missing field, which has no line, reports line 1); `field_value` is a known field with an invalid value (name, description, roles, version, encoding, extensions, standard-package metadata, or a package name that differs from the package) at that field's line; and `header` is a malformed header block (missing or unterminated delimiters, over the 4096-byte header bound, invalid YAML or an unsupported YAML feature) at line 1. `engine` is reported at line 1 before any other header field is checked. `detail` is diagnostic protocol JSON (including null, a string, an array or an object), not a machine-readable error discriminator. Implementations MAY choose different diagnostic text or structure. Portable conformance compares `code` and `line` exactly and validates the remaining error shape; it does not compare `detail` contents. Callers MUST branch on the code, not prose. A compiler MUST report a bounded error rather than crash on untrusted input. Normalized source validation and loader/discovery failures use their separate error codes; they need not have a body line.

## Normalized representation and identities

The compiled object contains `name`, `engine`, `roles`, `card`, `meta`, `role_metadata`, `types`, `states`, `terminals`, `any_rules`, `room_rules`, `any_meta`, `room_meta`, `extensions`, `digest`, and `source_digest`, plus `timeout`, `mentions: {max}`, `participant_types`, `role_sets`, `collections` and `contribution_profile` when declared. Maps are keyed by the declared names. Types contain `name`, `fields`, `meta`; fields contain `kind`, bounds or ordered values as appropriate, and `meta`. Text fields contain `encoding: "utf-8"`, `bytes: {min,max}`, nullable `graphemes` and an optional compiled `regex`. States contain `name`, ordered `rules`, ordered `deadlines`, nullable `max_entries: {limit,outcome}`, an optional `timeout`, an optional `total` (`band-contract/total/1`: `after_ms`, `outcome`, `meta`), and `meta`. Terminals, including the reserved outcomes, contain `name` and `meta`. Rules contain `id`, ordered `from`/`to`, `kind`, `matches`, boolean `event`/`each`/`activity`, nullable `bind`/`target`, an optional renewal `reset`, a `mention_cap` (an integer, or null for `mentions infinity`) only when the rule sets one, and `meta`. Duties contain `id`, `quantifier`, ordered `participants`, `kind`, `after_ms`, `outcome`, and `meta`.

A contract with [reply alternatives](#reply-alternatives) adds `contract.replies/1` to `card.extensions`, which stays sorted. A declaring head and its re-sends are ordinary rules; a head without `as` compiles with `bind` set to W's name. Each alternative is an ordinary rule in the wait state's `rules`: `from` the replier, `to` the requester, not an event, without `each`, with `bind` set to the block's common `as` name or null. It also carries `literals`, a map from field name to an enum member string or a JSON boolean (empty for a bare `kind()`), and `wait: "due-M"`; `matches` stays a map from field to binding and includes a reference the sugar supplied, so the sugared and explicit forms compile alike. The wait state's `rules` list the alternatives in source order, then the rules of its `state W` section. The wait state gains `wait: {id, heads, from, to, kinds, after_ms, outcome, alternatives, meta}`: `id` is `due-M`, `heads` and `alternatives` are rule IDs in source order, `kinds` are the alternatives' kinds in order of first appearance, and `meta` is the timeout alternative's metadata. Its `deadlines` are unchanged, because the wait is not a deadline row. Without a `state W` section, the compiler creates the state with the alternatives as its rules, no deadlines and no `max_entries`. The IDs are immutable and part of the digest. A contract without reply blocks has none of these keys, so its normalized form and digest are unchanged.

Canonical JSON is defined in the [engine specification](engine.md). `source_digest` is the SHA-256 of the exact UTF-8 file: header, body, documentation, comments and final newline ([file identity](file-identity.md)). `digest` is SHA-256 of the canonical compiled object before either digest field is inserted, after recursively removing every `meta`, `any_meta` and `room_meta` key, removing `description` and `version` from `card`, and removing extension permission and watch metadata. Role metadata retains each role's `name`. The digest is a structural behavior identity, not a solver for semantic equivalence; declaration ordering and generated rule IDs can change it. Both identities are required: a documentation-only edit keeps `digest` but changes `source_digest`.

Queries support the whole contract; type, state, outcome or role by name; room scope; and any-state scope. A role view includes the card/contract metadata, that role's metadata, all types, all outcomes, all states with only rules that involve the role as sender or recipient and duties that name it, and similarly filtered room/any-state rules. Unknown names return `not_found` or `unknown_role`. The runtime role view applies a separate accepted-fact visibility filter.

The portable query interface returns `{ok: value}` on success. Select the schema
for `value` by selector; these are different object shapes:

| Selector | Required name | Schema in `protocol-v2.schema.json` |
|---|---|---|
| `contract` | None | `compiled` |
| `role` | Card role | `role_definition` (the filtered definition, including its `role` metadata) |
| `type` | Type identifier | `type` |
| `state` | Nonterminal state identifier | `state_declaration` |
| `outcome` | Terminal outcome identifier | `terminal` |
| `room` | None | `scope_definition`: `{rules,meta}` for `room` |
| `any_state` | None | `scope_definition`: `{rules,meta}` for `anystate` |

Unknown roles return `{error:"unknown_role"}`. Unknown type/state/outcome names
or unsupported selectors return `{error:"not_found"}`. Queries are read-only;
they do not create a session, authorize a principal or advance a clock. The
[query-selector fixture](conformance/case-v2-query-selectors.json) exercises every
shape, absent names, an unsupported selector, and documentation on fields,
deadlines, roles, outcomes and scopes.

## Binding a contract to a Band room

DSL roles are protocol participants, not Band room permission roles. The
contract can declare `requester` and `assistant`; the creation configuration
binds them to distinct active agent participants. Humans stay outside contract
roles ([agents and extensions](agents-and-extensions.md)). Being a room
owner/admin does not grant a different contract role. Current membership and
the immutable binding are both required at every operation.

The caller submits the exact contract file once when creating a session. The
room compiles and pins its definition and source digests, supplies actor and
clock from trusted context, and stores the engine result durably. A declaration
cannot invite a participant, replace its own source, execute a program/model or
write ordinary chat outside the authority's accepted-message path. `room`
denotes a separate sessionless scope; it does not automatically activate event
hosting on the session routes. Band has not yet been qualified for this profile.
See the [Band binding](band-transport.md) and the
[checked integration example](band-integration.md) for the current mapping.
