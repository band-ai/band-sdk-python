---
name: band-python-comment-style
description: Band Python SDK comment style. Use when writing or editing comments or docstrings.
---

# Comment Style

Comments state facts about the code as it is now, not narration of how it
got there — never "extracted from X", "ported from Y", "changed from Z",
no session/PR/ticket/line-number history. Git already owns that history.

A comment earns its place only by saying something a reader can't get
from the code itself: a non-obvious invariant, a race/ordering guarantee,
a workaround for a specific external bug, a scope boundary that looks
like it should be wider than it is. Never restate what the code already
says in prose.

If a function needs a long comment to be understood, that's a signal the
function itself is doing too much — split it into named sub-functions
whose names carry the "what," and keep the comment for the one "why" that
can't be named away. Prefer trimming/removing this class of comment
outright over compressing it.

Docstrings follow PEP 257: a one-line summary first, in the imperative mood.
