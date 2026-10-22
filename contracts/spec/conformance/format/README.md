# Formatter vectors

Shared, normative vectors for [contract formatting](../../format.md). Each directory is
one case:

- `input.band`: a valid contract written untidily;
- `expected.band`: the only correct formatting of it;
- or, for Markdown, `input.md` and `expected.md`.

Every formatter MUST produce the expected file from the input file byte for byte and
MUST leave the expected file unchanged. Both files compile to the same digest and the same
definition apart from line numbers. The `input.band` files are deliberately
unformatted: a repository-wide `--check` or bulk reformat MUST skip them.

| Case | Covers |
|---|---|
| `01-indentation` | levels 0/1/2, tabs, fields, rules, events, `collect` options, `anystate`, rosters |
| `02-spacing` | `->`, `=>`, colons, the outcome bar, `enum(a\|b)` and sigils left alone, `{a,b}` selectors |
| `03-blank-lines` | leading and trailing blank lines, collapsed runs, exactly one blank line between every top-level declaration |
| `04-comments-and-notes` | comments and notes take the next declaration's level; a tab-indented note is kept |
| `05-documentation` | one-line, multiline and raw (`~s"""`) literals; a literal that cannot shift is kept |
| `06-frontmatter-and-final-newline` | frontmatter kept byte for byte, trailing spaces, a missing final newline |
| `07-non-ascii` | multibyte frontmatter kept byte for byte; non-ASCII comments, docs and literals; a literal led by no-break spaces shifts (a later tab is not leading whitespace) |
| `08-prompt-blocks` | one-line prompts at every level (a reply alternative's included) become `"""` blocks with their text unchanged (escapes kept); a `~s` prompt becomes a plain block, or a `~s"""` block when it holds a backslash; no blank line between a prompt and its item |
| `09-markdown` | ```` ```band ```` fences formatted in place (full contracts and indented fragments); prose, other fences, fragments without a top-level declaration and a fence that formatting would close are kept |
| `10-reply-alternatives` | syntax U: a send's `=>` alternative at level 2 and each `\|` alternative one space deeper, with prompts, comments and a `"""` block between them; one space around `=>`, `\|` and `&` on those lines; the implicit-correlation sugar kept as written |
