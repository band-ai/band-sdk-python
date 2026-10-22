# Literal sigils in the contract body

Sigils mark what a literal means before the reader reaches its contents. This
is contract data syntax for `compiler/2`, engine `0.2.0-lab.1`; it does not
execute Elixir. Sigils are lowercase: `~s`, `~r` and `~w`. The uppercase `~S`,
`~R` and `~W` were removed and are `syntax` errors. No sigil evaluates or
interpolates text, and this DSL does not inherit Elixir's upper/lowercase
evaluation distinction.

Write prompt metadata as `"""` blocks, never with a sigil. Do not add a sigil
unless its special behavior is needed: `~r/.../` writes a regular-expression
pattern without an extra string-escape layer. The raw `~s|...|` text described
below remains accepted, but prompts do not use it. Write lists as the presentation does:
`enum(approve|revise)`, `ref(request|revision)` and `{a,b}`; a `~w` word list
is also accepted. Keep short forms such as `5m` and `lead`.
Workflow operations remain ordinary words: `each`, `event`, `state` and
`room`. A sigil identifies a literal value; it does not hide an operation.

Start with the [one-review skill](start-here-v2.md), which needs no sigils.
The snippets below lead with the presentation's forms; the alternatives they
list are also accepted, not the recommended style. The [complete sigil
example](../contract-lab/contracts/v2/sigil-review.band) exercises the word and
pattern sigils. Its prompts are ordinary `"""` blocks, as in every example;
raw `~s` documentation remains supported for text that must keep backslashes or
`#{` literally.
The [prompt metadata guide](multi-agent-skills.md) defines ordinary strings and
how the agent uses the attached guidance.

## Raw documentation: `~s`

Prompts are `"""` blocks, never sigils. When a prompt quotes code, paths,
backslashes or text that looks like interpolation, escape it inside the block:
write `\\` for a backslash and `\#{` for `#{`. The contract prompt below decodes
to a line that reads
`Keep C:\work\notes, the two characters \n and #{request_id} exactly as written.`:

```band
"""
Keep C:\\work\\notes, the two characters \\n and \#{request_id} exactly as written.
"""
contract @30m => expired

"""
The agent returns "accepted" or "rejected".
"""
type verdict
  decision: enum(accepted|rejected)
```

The raw `~s` forms remain accepted for documentation; the rules below define
them. `~s` means **raw string**. A compiler MUST preserve backslashes and
interpolation-shaped text; it MUST NOT decode escape sequences, evaluate
expressions, invoke user-defined sigil handlers or create atoms/symbols from
the contents. Single-line forms add no newline. Their accepted delimiters are
`/.../`, `|...|`, `{...}`, `[...]`, `(...)`, `<...>`, `"..."`, and `'...'`.

The first unescaped closing delimiter ends a single-line literal. A backslash
protects the following byte from delimiter recognition, but both bytes remain
in the string. Paired delimiters do not nest. Prefer a different delimiter
when the content contains the closer. No modifier or trailing token is
supported. A malformed `~s` literal fails with `documentation` at its opening
body line.

A multiline raw form is also accepted: `~s"""` opens on its own line, and `"""` closes
on its own line. ASCII spaces/tabs may surround the closing delimiter. Its
leading indentation determines how many leading ASCII space/tab bytes to
remove from each content line, stopping early at the first other byte.
The opening and closing lines are excluded. All remaining content bytes and
line endings are preserved, except the line break before the closing line,
which is layout, as in a plain block. An empty
heredoc decodes to the empty string. The existing 262,144-byte body limit also
bounds raw documentation; the reader MUST NOT execute it to decode it.

Blank lines and `%%` comment lines outside the literal may separate it from
its declaration. A second pending literal or a pending literal at EOF fails
with `orphan_documentation`. Raw documentation is available on every existing
documentation target: contract, role, type, field, state, rule, duty, scope,
terminal, collection and declared extension metadata. Full and role-filtered queries MUST
retain the decoded string and its one-based opening body line.

## Lists of names: `~w`

The presentation writes a list inside the construct that gives it meaning:

```band
"""
Two reviewers each review a request or a revision of it.
"""
contract @30m => expired

"""
You collect the reviews. Check which request or revision each one answers.
"""
role lead

"""
You are one of two reviewers. Review the task you were given and name it in
your review.
"""
role a

"""
You are one of two reviewers. Review the task you were given and name it in
your review.
"""
role b

"""
The original task under review.
"""
type request

"""
A revised version of the task, sent after an earlier review asked for changes.
"""
type revision

"""
Your decision on one request or revision. Choose reject only when the work
cannot be fixed by a revision.
"""
type review
  decision: enum(approve|revise|reject)
  source: ref(request|revision)

"""
Each reviewer owes one review within five minutes.
"""
state reviewing
  {a,b} -> lead: review(source=$task) each @5m => collected | stalled
```

In this excerpt, `enum(approve|revise|reject)` permits one of three literal
values. `ref(request|revision)` permits an accepted message of either named
type; the message's `source` field must still match the ID bound to `$task`.
The complete example declares those types and binds the initiating message.

`{a,b}` names reviewer roles A and B. It does **not** mean wait for both.
`each` makes successful collection wait for one reply from each reviewer;
`@5m … | stalled` ends the job as `stalled` if either reply is missing at five
minutes. Without `each`, the first accepted reply takes the transition. When
the reviews should not leave the state, a timed event owes them instead:
`event {a,b} -> lead: review() each @5m => stalled`.

A `~w` word list is also accepted in each of these positions and compiles to
the same behavior:

```band
"""
Two reviewers each review a request or a revision of it.
"""
contract @30m => expired

"""
You collect the reviews. Check which request or revision each one answers.
"""
role lead

"""
You are one of two reviewers. Review the task you were given and name it in
your review.
"""
role a

"""
You are one of two reviewers. Review the task you were given and name it in
your review.
"""
role b

"""
The original task under review.
"""
type request

"""
A revised version of the task, sent after an earlier review asked for changes.
"""
type revision

"""
Your decision on one request or revision. Choose reject only when the work
cannot be fixed by a revision.
"""
type review
  decision: enum(~w[approve revise reject])
  source: ref(~w[request revision])

"""
Each reviewer owes one review within five minutes.
"""
state reviewing
  ~w[a b] -> lead: review(source=$task) each @5m => collected | stalled
```

The `~w` reader accepts the same eight single-line delimiters as `~s`.
ASCII spaces and tabs separate names; repeated and surrounding separators are
ignored. Newlines, escapes, interpolation and quoted names are not list syntax.
Paired delimiters do not nest. Lists MUST be nonempty and duplicate-free, and
MUST preserve source order. The result contains strings, never atoms/symbols;
type modifiers such as `~w[a b]a` are invalid. The whole-body byte limit bounds
the source, token count and decoded list. There is no multiline `~w` form.

Enum values and reference type names use the existing
`[a-z][a-z0-9_]*` identifier grammar. Unknown referenced types still fail with
`unknown_type`; invalid or duplicate field values fail with `type`.
Role lists select already-declared discovery roles by exact name, including
names containing hyphens such as `~w[review-lead reviewer]`. They neither create
roles nor select concrete actors. Duplicate roles fail with `duplicate`, an
undeclared name with `unknown_role`, and malformed literals with `syntax`.
`*` remains the separate all-roles selector; it has no wildcard meaning inside
a word list. Existing role bindings, self-recipient exclusion, permissions,
message quotas and `each` contribution tracking continue to apply.

Word selectors work on both sides of a message arrow, including timed events,
and in session, `anystate` and optional `room` event rules. A single role can
still be written simply as `lead`; a duration remains `5m`. Do not wrap those
already clear values merely to add punctuation.

## Compatibility and boundaries

Ordinary `"..."` and `"""` documentation use escape decoding and reject
interpolation. Use `~s` when those sequences should remain literal. The raw
`~r/.../` form remains the
[bounded regex literal](safe-patterns.md), with its own pattern grammar and
1,024-byte limit; `~r|...|` and `~r{...}` delimiters are also accepted when the
pattern contains `/`. `~s` cannot substitute for a regex.

These forms are body syntax. Discovery headers retain their existing YAML or
native field syntax, and message payloads remain JSON. A sigil does not read a
file, import a module, fetch a URL or grant a permission. Unknown sigils fail;
they are never dispatched to executable plugins.

Documentation does not enter the behavior digest. Equivalent decoded metadata
has the same query value regardless of spelling, while changing the authored
bytes changes the exact source-file SHA-256. Existing session pins MUST NOT be
rewritten; a source revision is installed for a new session or explicit host
migration. Equivalent word-list and plain list spellings MUST produce the same
compiled values and behavior digest. No compiled schema or JSON payload shape
changes, and no previously accepted contract acquires different runtime rules.
The [documentation tests](../contract-lab/test/documentation_sigil_test.exs)
check exact text, indentation, line endings, metadata queries, malformed input,
source identities and absence of atom creation.
The [word-list tests](../contract-lab/test/word_sigil_test.exs) additionally check
ordered enums/references, declared-role selection, successful group replies,
wrong-reference rejection, duplicates and deadline boundaries.

Multiline author comments use `/* ... */` on their own lines. They are ignored by queries; retained documentation supplies agent prompts. See [multi-agent skill guidance](multi-agent-skills.md).
