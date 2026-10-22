# Bounded pattern validation

Engine `0.2.0-lab.1` supports an optional full-string regular-language validator
on a UTF-8 text field. This is deliberately a **restricted regex dialect**,
not a call into the host language's general-purpose regular-expression engine.

```band
"""
Exchange items that carry a short machine-readable code.
"""
contract @30m => expired

"""
One item identified by a code: uppercase letters, digits, underscores and
hyphens, starting with a letter.
"""
type item
  code: text(bytes: 64, graphemes: 32, regex: ~r/[A-Z][A-Z0-9_-]*/)
```

The byte bound remains mandatory. Byte, optional grapheme and optional pattern
constraints MUST all pass. `regex` is the final text argument. Write patterns
with `~r` so their regular-expression meaning is explicit and their backslashes
do not pass through an extra string-escaping layer. Matching covers the
entire decoded value; it is never an unanchored search. It performs no normalization,
case folding, replacement, capture extraction, callbacks or code execution.

## Raw pattern literals

The authoring syntax is inspired by [Elixir sigils](https://elixir.hexdocs.pm/sigils.html).
Write a raw pattern as `~r/.../`, as the presentation does, and escape a `/`
inside the pattern as `\/`. Other delimiters are also accepted; the uppercase
`~R` and quoted patterns were removed. See
[literal sigils](sigils-v2.md#compatibility-and-boundaries).

```band
"""
Exchange values whose format the room checks before it accepts a message.
"""
contract @30m => expired

"""
Examples of checked formats. Fill each field in exactly the format it states;
the room rejects anything else.
"""
type pattern_examples
  code: text(64, regex: ~r/[A-Z][A-Z0-9_-]*/)
  sum: text(64, regex: ~r/a\+b/)
  quote: text(64, regex: ~r/"yes"/)
  path: text(256, regex: ~r/\/api\/[a-z]+/)
  route: text(256, regex: ~r/\/api\/(cat|dog)/)
```

The sigil reader MUST preserve every pattern byte between the delimiters,
including quotes and backslashes. It MUST NOT decode string escapes,
interpolate expressions, evaluate Elixir, or call a native regex compiler.
Thus `\+` reaches the pattern compiler unchanged and matches a literal plus;
`\n` reaches it unchanged and matches a line feed. A literal backslash still
uses the regex spelling `\\`, with no further doubling for a string literal.
Regex syntax still needs its own escapes; raw sigils remove the additional
string layer, not the distinction between operators and literal characters.

The first unescaped closing delimiter ends the sigil, including inside a
character class. Backslash pairs are scanned together; an escaped delimiter
stays escaped in the pattern. For example, `~r/a\/b/` matches `a/b`.
Braced delimiters do not nest. Whitespace
outside the sigil is permitted; modifiers, other delimiter
forms and trailing tokens MUST fail with `pattern_syntax`. A sigil must fit
within the field's single-line declaration.
The reader MUST enforce the 1,024-byte pattern limit before scanning its body.

Portable JSON representation of a compiled pattern still escapes strings as
JSON requires; the raw sigil concerns the authored contract, not its wire
encoding.

## Supported language

The grammar supports Unicode scalar literals, concatenation, `|`, grouping
with parentheses, `*`, `+`, `?`, `.`, classes such as `[A-Z]`, and negated
classes such as `[^0-9]`. Dot matches one scalar other than line feed. Ranges
are inclusive Unicode scalar ordinal comparisons, independent of locale or
Unicode character-property tables. A hyphen is literal at the end of a class
or when escaped. A backslash can escape a metacharacter or introduce `n`, `r`
or `t`. A character class must contain at least one element; reversed ranges
are invalid. Parentheses do not capture.

Empty patterns or alternatives, anchors, counted repetition, backreferences,
lookaround, inline flags, lazy/possessive quantifiers, shorthand classes such
as `\w`, and Unicode property escapes are unsupported. The compiler MUST reject
them with `pattern_syntax`; it MUST NOT silently reinterpret or approximate
them. Whole-value matching makes `^` and `$` unnecessary. Bounds such as the
maximum number of characters belong in the text bounds where possible.

## No backtracking at runtime

An implementation MUST compile the accepted language into a finite automaton
before accepting a contract. This profile emits a deterministic finite
automaton (DFA): one state at a time and at most one transition for each input
scalar. Runtime validation MUST consume text only forward. It MUST NOT rewind
input, recursively try alternative matches, build capture stacks, compile a
pattern on demand, or fall back to a backtracking engine.

Consequently `(a+)+b`, `(a|aa)+b` and `(a*)*b` do not acquire exponential
matching behavior on a long string of `a`. Blacklisting a few suspicious
spellings is insufficient; the execution model supplies the guarantee.

The [RE2 design](https://github.com/google/re2/blob/main/README.md) and
[Thompson automaton explanation](https://swtch.com/~rsc/regexp/regexp1.html)
describe why avoiding backtracking changes the worst case. This implementation
uses its own smaller, specified DFA dialect; it does not claim RE2 or PCRE
syntax compatibility.

## Compilation and memory also have limits

Determinization can grow exponentially, so removing runtime backtracking alone
is insufficient. This profile fixes all of these limits:

| Resource | Maximum |
|---|---:|
| Decoded pattern source | 1,024 UTF-8 bytes |
| Parenthesis nesting | 16 |
| NFA states before determinization | 128 |
| Character-class elements | 64 |
| Alphabet partitions | 256 |
| DFA states | 128 |
| DFA transitions in total | 4,096 |
| Charged compilation work for one pattern | 200,000 units |
| Charged compilation work across one contract | 1,000,000 units |
| Charged pattern work across one message type | 1,000,000 units |

Limits MUST be checked before the bounded allocation or iteration. Exceeding
one produces `pattern_limit` at compilation; the contract is not installed.
The compiler does not promise to accept every mathematically regular pattern.
It bounds parsing, intermediate automata, the exploration queue and final
transitions. Counted repetitions cannot trigger huge expansion because they
are outside the dialect. No compressed pattern format is accepted.

For each candidate alphabet interval in a DFA state, compilation precharges
`3 * N + R + E`, where `N` is the NFA node count, `R` is the total number of
NFA character ranges and `E` is the number of epsilon edges. This bounds the
predicate scans and visited-set closure work; a closure visits each NFA node
once. The fixed source/node limits also bound sorting and set bookkeeping.
All patterns in one contract draw on one compilation budget, in source order.
A pattern may spend at most the per-pattern limit and at most what the
contract has left. The pattern that would exceed either produces
`pattern_limit` on its field line, so a source made of many near-limit
patterns cannot multiply compilation work.

For runtime, let `S` be the number of DFA states and `T` the largest number of
outgoing transition intervals in a state. The compiled `cost_per_scalar` is
`S + T + 2`. For a field with maximum UTF-8 length `B`, `max_steps` is
`(B + 1) * cost_per_scalar`. The compiler MUST reject a message type if the sum
of these bounds over its patterned fields exceeds 1,000,000, with
`pattern_limit` on the message type's line. Valid UTF-8 has
at most `B` scalars. State lookup charges its list position plus one; each
tested interval charges one. The matcher enforces the step ceiling as a
second guard. No valid compiled pattern needs a wall-clock-dependent decision.

These are deterministic operation limits, not a millisecond latency promise or
protection from operating-system resource exhaustion. Host request-size,
concurrency, admission-rate and compilation-cache policies remain necessary.
Hosts MUST NOT claim a valid input because compilation or validation ran out
of resources, and MUST NOT retry it with a less restricted engine.

## Failure and portable representation

Byte and grapheme checks precede pattern matching. A non-match rejects the
proposal as an invalid payload. Invalid input and a runtime limit breach also
fail validation; no message receipt, business quota charge or effect follows
from that failed proposal. Independently due session timers may still advance.
The low-level matcher distinguishes `invalid_text` and `validation_limit` for
diagnosis. A runtime guard failure for a normally compiled, bounded value is an
implementation/integrity problem to investigate, not evidence of agent intent.

The field's optional `regex` member is a `band-contract/pattern-dfa/1` object
with `source`, `states`, `max_bytes`, `cost_per_scalar`, and `max_steps`.
State zero is initial. Each state has boolean `accept` and sorted, disjoint
`transitions`, each `[lowest_scalar, highest_scalar, target_state]`. Missing
transitions reject. The compiler's deterministic breadth-first state numbering
and ascending alphabet partitions define the reference representation; it is
part of the structural behavior fingerprint. It is data, never a native regex
object. Hosts obtain it from a supported compiler, not untrusted message input.

[Executable evidence](../contract-lab/test/safe_pattern_test.exs) includes
adversarial repetition, compile-growth limits, explicit guard failure,
Unicode/multibyte inputs, aggregate field work, and exhaustive small-word
comparisons with a separate matching implementation. Pattern support first
failed six tests before implementation. Passing those tests complements the
bounded algorithm; timing a few patterns alone would not establish safety.
[Literal tests](../contract-lab/test/pattern_literal_test.exs) cover raw quotes,
regex escapes, delimiter choices, invalid literals, byte/work bounds and
compatibility with the earlier spelling. The new literal behavior first failed
five of six tests, then all six passed with the bounded reader implemented.

The [literal-sigil guide](sigils-v2.md) also defines `~s` raw documentation and
`~w` name lists. Their context is different: only `~r` supplies a pattern, and
its DFA restrictions remain mandatory.
