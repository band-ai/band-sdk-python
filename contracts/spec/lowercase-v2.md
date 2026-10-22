# Lowercase contract keywords

For engine `0.2.0-lab.1`, keywords are lowercase throughout the body:

```band
"""
Give two reviewers one task and collect a review of it from each, with at most
three review rounds.
"""
contract @1h => expired

"""
You hand out the task and collect the reviews. Keep each review tied to the task
it answers.
"""
role lead

"""
You are one of two reviewers. Review the task you were given, send progress
notes if it takes a while, and send your review within five minutes.
"""
role a

"""
You are one of two reviewers. Review the task you were given, send progress
notes if it takes a while, and send your review within five minutes.
"""
role b

"""
The task to review and what the lead wants to know about it.
"""
type request

"""
A reviewer's findings on the task; source names the task it reviews.
"""
type review
  source: ref(request)

"""
A short note on where the review stands. It never replaces the review.
"""
type progress

"""
The lead sends the task to both reviewers.
"""
state new
  lead -> {a,b}: request() as task => reviewing

"""
Both reviewers owe a review of the task. The phase has ten minutes, and the
run may enter it at most three times.
"""
state reviewing @10m => stalled max_entries 3 => exhausted
  {a,b} -> lead: review(source=$task) each @5m => collected | stalled
  event {a,b} -> lead: progress()

end collected

end stalled

end exhausted
```

The lowercase forms `as`, `each`, `event`, `mentions`, `max_entries`, `on`,
`online`, `offline`, `removed` and `for` MUST be accepted in their grammar
positions. Existing lowercase declarations (`contract`, `role`, `type`,
`state`, `end`, `anystate`, `room`, `watch` and `require`) remain unchanged.
Timed events and host observations are lines inside a state. This excerpt
needs the `host.presence/1` extension in its header:

```band
"""
Two reviewers review for the lead, and the review pauses when a reviewer goes
away.
"""
contract @30m => expired

"""
You collect the reviews. If a reviewer goes offline the review pauses; wait for
it rather than reviewing on its behalf.
"""
role lead

"""
You are one of two reviewers. Send your review within five minutes and stay
online while you work.
"""
role a

"""
You are one of two reviewers. Send your review within five minutes and stay
online while you work.
"""
role b

"""
A reviewer whose presence the room watches while the review runs.
"""
role reviewer

"""
A reviewer's findings, with the evidence behind them.
"""
type review

"""
Each reviewer owes a review within five minutes; a reviewer going offline for
five minutes pauses the run.
"""
state reviewing
  event {a,b} -> lead: review() each @5m => stalled
  on reviewer offline for 5m => paused

"""
The review is paused while the reviewer is away. It resumes when the reviewer
is back online.
"""
state paused
  on reviewer online => reviewing

end stalled
```

Keywords are lowercase only. Uppercase spellings and plus-prefixed declarations
were removed: each is a `syntax` error whose detail names the lowercase form.
Arbitrary mixed case such as `Each` is not supported.

Case handling applies only to keyword positions. Role/type/field names,
bindings, documentation, pattern text, enum values and literal lists MUST NOT
be rewritten. A role named `event` can still be a sender. Use ordinary
strings by default. When raw behavior is needed, use `~s` and `~r`, and `~w`
for name lists; the uppercase `~S`, `~R` and `~W` are rejected.
New coordination syntax also uses lowercase `collect`, `reply`, `offline`,
`max` and `infinity`. Collect `failure type()` was removed; collections
report failures through [contribution outcomes](contribution-outcomes.md).

Presentation examples use syntax highlighting for keywords, durations,
references, literal values and comments. Highlighting MUST preserve the exact
copyable source text and MUST remain readable without color. It does not imply
that an editor or language server integration has shipped.

Evidence: [lowercase keyword tests](../contract-lab/test/lowercase_keywords_test.exs).
