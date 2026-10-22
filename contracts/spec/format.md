# Contract formatting

This document defines the canonical layout of a `.band` file. A formatter rewrites
whitespace only: the formatted file MUST compile to the same `digest` and the same
compiled definition, apart from source line numbers and `source_digest`. Formatting a
formatted file MUST NOT change it. The [shared vectors](conformance/format/README.md)
are normative: every implementation MUST turn each `input.band` into its
`expected.band` byte for byte, and MUST leave each `expected.band` unchanged.
The reference implementation is `mix contract.format [--check] [PATH...]`.

## Scope

- **Frontmatter.** A file that starts with `---` followed by a newline keeps its
  frontmatter, through the closing `---` line, byte for byte. A file without
  frontmatter is formatted as a body.
- **Line endings.** Only LF files are formatted. A formatter MUST refuse a file that
  contains CR, and a file that is not valid UTF-8, rather than rewrite it.
- **Validity.** A tool that can compile SHOULD refuse a file that does not compile and
  MUST NOT write a result whose digest or definition differs.

## Lines

The body is read line by line, as the compiler reads it. *Whitespace* is the Unicode
`White_Space` set: U+0009 to U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000 to U+200A,
U+2028, U+2029, U+202F, U+205F and U+3000. *Trimming* removes whitespace from both ends
of a line, and a line's *leading whitespace* is the whitespace before its first other
character.

- a **blank** line is empty after trimming;
- a **comment** starts with `%%`;
- an **author note** starts with `/*` and runs through the first line containing `*/`;
- a **multiline literal** is a line that is exactly `"""` or `~s"""`, through the next
  line that is `"""` after trimming;
- a **one-line literal** starts with `"` or `~s`;
- anything else is a **declaration**.

## Indentation

Indentation is two spaces per level and never a tab.

| Level | Declarations |
|---|---|
| 0 | `contract`, `role`, `type`, `state`, `anystate`, `room`, `end`, `watch`, `require`, and a roster `name: list(type)` |
| 2 | Lines directly after a `collect` (or after one of its options) that start with `reply`, `offline`, `quorum` or `failure` and contain no `->`; reply alternatives: every line that starts with `=>` or `\|`, and a `\|` line is written one space deeper than its level, so the alternatives line up under the first |
| 1 | Every other declaration: fields, rules, events, `collect`, `on` triggers |

A keyword followed by ` -> ` is a rule sent by a role of that name, not a declaration
of that keyword. No other declaration starts with `=>` or `|` (a timed rule's outcome
`|` is mid-line), so an alternative's level never depends on the lines before it: under
a send at level 1, `=>` sits at 4 spaces and each `|` at 5. Comments, notes and
literals take the indent of the next declaration, skipping other comments, notes,
literals and blank lines, so before a `|` alternative they also sit at 5 spaces; at the
end of the file they take level 0.

A one-line comment or literal is trimmed and re-indented. An author note or multiline
literal moves as a whole: every line shifts by the same number of spaces, so the
decoded documentation is unchanged. Lines that are empty stay empty. When a line's
leading whitespace contains a tab, or a line has fewer leading spaces than a leftward
shift needs, the whole note or literal is kept byte for byte. Content lines of a
multiline literal keep their trailing whitespace; its delimiter lines and note lines
lose it.

## Prompts

Prompts (documentation literals) are always written as blocks. A one-line literal is
rewritten as a multiline literal at the same level: a `"""` line, the literal's content
exactly as written (escapes included), and a closing `"""` line. A `~s` one-line literal
becomes a plain `"""` block when its content holds no backslash and no `#{`, and a
`~s"""` block otherwise, so its text reads the same. A one-line literal with anything
after its closing delimiter, or whose content is `"""` after trimming, is kept as
written. Because a block's text ends at its last content line (the line break before
the closing `"""` is not part of it), the rewritten prompt decodes to exactly the same
text.

## Spacing inside a declaration

Sigils (`~r`, `~s` and `~w` with any of their delimiters) are copied unchanged. In the
rest of the line:

1. runs of spaces and tabs become one space;
2. `->` and `=>` have one space on each side;
3. a colon has no space before it and one after it (`name: value`, `a -> b: msg()`,
   `text(bytes: 1..32)`);
4. `|` and `&` outside parentheses have one space on each side (`=> done | late`,
   `| error() & failed`); inside parentheses they are unchanged, so `enum(a|b)` and
   `ref(x|y)` stay compact;
5. the line is trimmed.

Commas, `{a,b}` selectors, arguments such as `(task=$task)` and letter case are kept as
written.

## Blank lines

The body is divided into top-level units. A unit is a level-0 declaration with the
level-0 comments, notes and literals before it (and any blank lines among them) and
every nested line after it. Level-0 comments, notes or literals with no declaration
after them form a unit of their own.

- There is exactly one blank line between two units, so consecutive `role`, `type`,
  `state` or `end` declarations are never grouped.
- A prompt sits directly above the item it documents: blank lines after a literal are
  removed.
- Inside a unit, a run of blank lines becomes one blank line.
- The body has no blank line at its start or end, and the file ends with exactly one
  newline.

## Markdown

In a `.md` file a formatter formats the content of each fenced code block whose info
string is `band`: an opening line of three or more backticks or tildes followed by
`band`, through the next line made only of at least as many of the same character.
Every other byte, including the fence lines, prose and fences with any other info
string, is kept. The fence's indentation is removed from its content before formatting
and added back to each non-blank line after it. A fence whose content is blank, has no
top-level declaration (a fragment of nested lines or header fields), has a non-blank
line indented less than the fence, or would gain a line that closes the fence, is kept
as written. Nothing inside any fence is parsed as Markdown, and an unclosed
fence runs to the end of the file. A fence that compiles before formatting MUST compile
to the same digest and definition after it.

## Checking

`--check` writes nothing. It exits 0 when every file is already formatted and 1 when
any file would change, listing those files. Errors are reported as JSON on standard
error with exit status 1, like the other `mix contract.*` commands.

A directory walk takes regular `.band` files only. It skips entries that start with a
dot, `deps`, `_build`, `node_modules`, symlinks and special files, and the paths listed
in any `.band-format-ignore` file in a walked directory or one of its ancestors. Such a
file holds one pattern per line, relative to its own directory; `*` and `?` match within
one path segment, never a leading dot; blank lines and lines starting with `#` are
ignored, and a listed directory is skipped whole. Files named on the command line are
always formatted. Sources whose bytes are pinned by a hash elsewhere, and the
deliberately unformatted vector inputs, belong in such a file.
