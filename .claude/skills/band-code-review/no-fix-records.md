# No-fix records

Shared by `band-code-review`, `band-pr-triage`, and `band-here-is-a-review`.

A no-fix record keeps one claim the skeptic investigated and dropped, on the
PR itself. Later cycles and runs skip that claim while the code its verdict
rests on is unchanged, and look at it fresh once that code changes.

Only a claim that isn't real becomes a record: it didn't reproduce, or no
real caller reaches it. A proven, reachable defect never does, even when it
is out of scope; it is an out-of-scope finding (`band-code-review`, "Comment
bar").

## Format

One inline comment in the cycle's review, anchored on a changed line of the
claim's flow, resolved once the review is submitted:

    no fix: <the claim, one sentence>. <why it fails, citing file:line: the guard that prevents it, why no real caller reaches it, or that it didn't reproduce>.

    <!-- band-code-review:no-fix {"sha":"<head SHA>","footprint":[["<path>",<start>,<end>]]} -->

- `sha` is the head the skeptic judged.
- `footprint` lists every line range at `sha` the verdict rests on: the
  claim's own lines plus each guard, caller, or test the reason cites. A
  range left out is a change that goes unnoticed.

GitHub hides the HTML comment when rendering; read it from the raw `body`.

A thread is a record when its first comment carries the marker and nobody
else has replied. Once anyone else replies it is an ordinary thread, open or
settled by its resolved state like any other.

## Closed or reopened

Test each record against the PR head:

    git diff -U0 <sha> <head SHA> -- <footprint paths>

It is **closed** when the command succeeds and no hunk's old side overlaps a
footprint range. The old side of `@@ -a,b +c,d @@` covers lines `a` to
`a+b-1`; `b` omitted means 1; `b` = 0 is an insertion after line `a`, counted
as line `a`. Anything else **reopens** it, errors included (an unknown `sha`
after a force-push, a renamed path).

- **Closed** — the claim is settled. The agents that propose hypotheses (the
  Edge-Case mapper and Logical-Bugs explorer) get one `claim — file:line`
  line per closed record, never its reason, headed: "Already investigated
  and dropped; the code it rests on is unchanged. Don't re-propose these.
  Any other claim about the same code is in scope."
- **Reopened** — ignored entirely: no agent sees it and no filter uses it, so
  the code gets a fresh look. The thread stays on the PR as history.

A hypothesis matches a record only when it describes the same failure
mechanism and consequence; touching the same lines is not a match.
