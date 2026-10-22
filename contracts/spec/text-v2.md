# UTF-8 text bounds

This extension applies to `compiler/2`, engine `0.2.0-lab.1`.

## Mandatory byte limit, optional grapheme limit

Every text field MUST declare a finite maximum number of **UTF-8 bytes**.

```band
"""
An agent returns a written answer with a brief, a title and a required section.
"""
contract @30m => expired

"""
Your answer. Keep the title short enough to read at a glance, put the details
in the brief, and always fill in the required section.
"""
type answer
  brief: text(4000)
  title: text(bytes: 256, graphemes: 80)
  required: text(bytes: 1..4000, graphemes: 1..1000)
```

`text(4000)` and `text(bytes: 4000)` allow zero through 4,000 bytes inclusive.
A range explicitly supplies the minimum. `graphemes` has the same number or
range syntax and is optional; when present, **both** bounds MUST pass. Each
bound is a nonnegative integer no greater than 9,007,199,254,740,991, and the
minimum MUST NOT exceed the maximum. A zero maximum permits only empty text.
Omitted, infinite, reversed, overflowing and grapheme-only byte bounds MUST
be rejected by the compiler. Byte limits are checked before grapheme counting.

The limit applies to the decoded string re-encoded as UTF-8, without Unicode
normalization. It excludes JSON delimiters and carrier escape spelling. Thus
literal `🚀` and JSON `\ud83d\ude80` both count as four bytes and one grapheme.
Precomposed `é` counts as two bytes; `e` followed by a combining acute accent
counts as three. Both count as one grapheme. Invalid UTF-8 and lone surrogate
values MUST be rejected, never repaired or silently transcoded.

The existing pinned Unicode **16.0.0** extended-grapheme algorithm applies.
An implementation MUST NOT substitute its host runtime's newer Unicode table.
Graphemes approximate perceived character count; they do not guarantee pixel
width, number of lines or font-dependent display area. A long combining-mark
sequence may occupy one grapheme; the mandatory byte limit still bounds it.

## Encoding declaration

`encoding` is a reserved discovery field, defaulting to the single supported
value `utf-8`. Frontmatter spells it `encoding: utf-8`; standard packages use
metadata `band-encoding: "utf-8"`.
Only that exact value is currently valid. Other encodings and alternate
spellings MUST be rejected. A future extension requires explicit versioning;
an unknown value is not permission to negotiate or guess an encoding.

The resolved compiled card always includes `encoding: "utf-8"`. Each text
field exposes `kind: "text"`, `encoding: "utf-8"`, a required `bytes` object
with `min` and `max`, and `graphemes` containing either the same bound shape or
null. Full and role-filtered definitions expose the same constraints.
An explicit default and an omitted default have identical behavior digests;
their exact file hashes still differ.

A consumer may convert accepted text for its own use. Limits on that other
encoding are outside this protocol. Whole-envelope transport limits remain
separate from each decoded field's UTF-8 bound. The completion reservation in
[defaults](defaults-v2.md#reserve-known-response-capacity) counts messages, not
bytes. Creation separately refuses, with `completion_size`, a session whose
`max_bytes` is below the smallest envelope of a reply that a duty can require.
The check uses each field's minimum, so it refuses only a session in which no
reply of that kind can fit; it does not prove that every valid reply fits.
Choose field bounds so that the largest reply you expect fits.

An optional `regex` argument adds the [bounded pattern validator](safe-patterns.md).
Its restricted grammar, compilation limits and deterministic matcher are
mandatory; implementations cannot substitute an ordinary backtracking regex.

## Evidence

[The executable tests](../contract-lab/test/utf8_text_test.exs) cover ASCII,
multibyte characters, emoji, combining marks, invalid UTF-8, Unicode-version
boundaries, optional grapheme constraints, JSON escape equivalence and field
queries. The new text syntax produced eight failing tests before implementation.
The [v2 schema](schemas/protocol-v2.schema.json) describes resolved fields;
implementations must enforce its semantic byte and grapheme constraints, not
substitute JSON Schema's code-point `maxLength`.
