# Multi-agent skills

A **multi-agent skill** combines guidance about how to do useful work with an
enforceable agreement about how agents coordinate. Its contract is the part the
room can check: roles, message shapes, states, obligations, outcomes and time.
The prompts explain purpose, expertise, evidence, uncertainty and how to use the
results. Neither part replaces the other.

The room exposes both through discovery and role queries. An ordinary authorized
agent can read its guidance and current legal messages without using the optional
program-replacement client. Instructions in a skill do not grant tools, identity,
room access or permission to perform an external action.

## Guidance is part of the skill

Write each prompt as a `"""` block directly above the declaration or rule it
guides. Give the contract, every role, every message type and every state a
prompt:

```band
"""
Combine independent reviews into one summary for the lead.
"""
contract @30m => expired

"""
You run the review and receive the summary. Act on supported findings and
weigh the gaps the summary names.
"""
role lead

"""
Work independently. Cite evidence and state uncertainty.
Use your assigned specialty; name anything you could not check.
"""
role reviewer

"""
The combined findings: what is supported, where reviewers disagreed and what
nobody checked.
"""
type summary

"""
Combine supported findings. Preserve disagreement and name missing coverage.
"""
state summarizing
  """
  Send the findings, their evidence and the remaining gaps.
  """
  lead -> reviewer: summary() => complete
```

The parser attaches the decoded text as **prompt metadata** (`meta.doc`) to the
next item, with the literal's opening body line in `meta.line`. It does not
attach to the preceding item, become a standalone message, or spill into later
items. Blank lines and author-only comments may intervene; another pending
literal or a literal with no following item is an error. Prefer direct adjacency
in authored skills so the relationship is visible.

The same attachment applies to the whole skill (`contract`), roles, types,
fields, states, message rules, [reply alternatives](contract-dsl.md#reply-alternatives),
collections and outcomes. Full queries, role
queries and the current agent view MUST retain the metadata. Participating
agents use this text as guidance: business rules, evidence standards, judgment,
uncertainty and other work the room's structural contract cannot directly check.
The room retains and exposes the prompt; it does not execute it, turn its prose
into admission predicates, certify compliance or grant external permissions.

A reply block's wait state takes its prompt from its `state W` section when it
has one, and otherwise from the prompt above the send that awaits it. A wait
state with no section, such as the default `request_wait`, is therefore
prompted by prompting its send, and that satisfies the every-state rule. A
prompt, comment or note before an alternative sits at that alternative's
indent: 4 spaces before `=>` and 5 before `|` under a state rule.

```band
"""
One review whose decision picks the outcome.
"""
contract @1h => expired

"""
You send the change and act on the decision.
"""
role lead

"""
Review the change and decide, with evidence.
"""
role reviewer

"""
The change and the question.
"""
type request
  text: text(1..2000)

"""
Your decision on that request.
"""
type review
  request: ref(request)
  decision: enum(approve|revise)

"""
The lead sends the change.
"""
state new
  """
  Send the change. While you wait, the reviewer owes one review within five
  minutes.
  """
  lead -> reviewer: request()
    """
    Approve only what you checked.
    """
    => review(decision=approve) & approved
     """
     Say which changes are needed.
     """
     | review(decision=revise) & changes_requested
     | @5m & stalled

"""
The reviewer approved the change.
"""
end approved

"""
The reviewer asked for changes.
"""
end changes_requested

"""
No review arrived in time.
"""
end stalled
```

The block is the canonical prompt form, and `mix contract.format` writes every
prompt as one. The compiler still accepts a one-line `"..."` string, and it
decodes to the same text as its block. A one-line string must occupy its own
source line, with no trailing code. It uses the same escape decoding as ordinary
multiline documentation and adds no implicit newline. Multiline opening and
closing triple quotes each occupy their own lines, and a block's text ends at
its last content line. The decoded
value must be valid UTF-8. Interpolation, expressions, concatenation and extra
tokens are rejected. This one-line form is supported in `0.2.0-lab.1`.

Do not write a prompt with a sigil. Inside a `"""` block, write `\\` for a
backslash and `\#{` for text that looks like interpolation. Plain names and short lists remain
the defaults. Write regular expressions with `~r` to identify the pattern literal.
Sigils are lowercase. See the
[optional sigil forms](sigils-v2.md).

Changing only prompt text or using an equivalent literal spelling preserves
the behavior digest but changes the exact source hash. Existing sessions keep
their pinned source. No metadata schema change is required. The
[plain-prompt tests](../contract-lab/test/plain_documentation_test.exs) check
attachment, agent views, decoding, source identity and the admission boundary.

`/* ... */` supplies multiline **author-only comments**, on lines of their own.
They may span multiple lines or fit on one line. They do not nest, cannot have
code after their closing delimiter, and do not appear in role queries. `%%`
line comments remain supported. A comment does not consume pending guidance.
Use retained documentation for prompting; an ignored comment cannot teach a
participating agent. Comment delimiters inside documentation are literal text.

The [one-review skill](../contract-lab/contracts/v2/one-review.band) documents a
small two-agent workflow. The [review-lanes skill](../contract-lab/contracts/v3/review-lanes.band)
adds runtime participants, specialized assignments and partial collection.
The [renewable work skill](../contract-lab/contracts/v3/renewable-work.band)
shows that timer renewal applies to jobs in any domain, not just reviews.

## Where guidance travels

Only a rule's string travels with an accepted message. The accepted decision
carries the rule's attached metadata as `metadata`, so `metadata.doc` is that
rule's string, or `null` when the rule has none ([engine](engine.md)). Guidance
on the contract, roles, types, fields, states and outcomes stays out of the
message. An agent reads it in its role view, under `view.definition`.

A rule has one string, and it serves both parties. The sender reads it as a
hint about a legal move, and the recipient reads it when the message arrives.
Write it for both readers:

```band
"""
One reviewer reviews one immutable change for a lead.
"""
contract @30m => expired

"""
You own the change. Send exactly one immutable change, then decide based on
the review you get back.
"""
role lead

"""
Review exactly the change you were sent. Cite evidence for every finding and
say what you did not check.
"""
role reviewer

"""
The change to review, fixed at the moment it is sent.
"""
type request
  text: text(1..2000)

"""
The reviewer's findings on that change, with evidence. request names the
change reviewed.
"""
type review
  request: ref(request)
  text: text(1..8000)

"""
Start one review of one immutable change.
"""
state new
  """
  Lead: send one immutable change. Reviewer: review exactly that change within
  twenty-five minutes.
  """
  lead -> reviewer: request() awaits reviewing
    """
    Reviewer: cite evidence. Lead: read the review before you decide.
    """
    => review() & complete
     | @25m & stalled

"""
The reviewer is working on the change. The lead waits for the review.
"""
state reviewing

end complete

end stalled
```

When the lead sends `request`, the accepted decision's `metadata.doc` is the
rule's string, not the state's. A reply alternative is a rule too, so the
accepted `review` carries the string written above its `=> review()` line. The
state's string reaches the reviewer only through `view.definition`. A skill that puts guidance only on states therefore
sends every accepted message with `metadata.doc: null`. The
[guidance placement test](../contract-lab/test/guidance_placement_doc_test.exs)
runs this example.

## Runtime participants use a reusable type

Define shared prompting and an attribute schema with `type reviewer: agent`;
bind a nonempty runtime list with `reviewers: list(reviewer)`. The number of
members belongs to each run, not to the source file. The room validates every
member's attributes at creation. Type guidance, roster guidance and the
host-supplied assignment reach the member together.

Several rosters may reuse the same participant schema. Different general-review
members may intentionally use different models. The room identifies authenticated
agents and validates declared attributes; it does not certify expertise,
independence or quality from a model name. See [participant types](coordination.md).

## Collect a result for each participant

A collection keeps each useful reply and a status for every member. It closes
when all members settle or the deadline arrives, then enters an ordinary active
state such as `summarizing`. That state's guidance explains how to combine the
reports, preserve disagreement and name failed, offline or timed-out coverage.
Receiving a report or closing a collection does not establish that the work is
correct or that a change is approved.

Declare `room.contributions/1` and specify the successful `reply` shape. A member
can explicitly stop its own contribution; a trusted host can report that the
assigned work ended unsuccessfully. These standard room controls keep the
original cause type and optional evidence reference. An agent does not need to
invent or send `unable()` after a system failure. Recoverable errors leave the
contribution pending. See [contribution outcomes](contribution-outcomes.md).

Read the exact [roster and collection rules](coordination.md),
[renewable-clock rules](renewable-timeouts.md) and
[portable integration profile](coordination-profile.md).

The repository's authoring skills in `skills/` apply this guidance: the
`multi-agent-contracts` skill routes to sub-skills that create a contract from
an agreed workflow or a chat history, iterate on an existing contract, or
replace a participant with a program.
