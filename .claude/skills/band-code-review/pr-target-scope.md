# Resolve the target and scope

Shared by `band-code-review` and `band-pr-triage`. Run once, before spawning anyone.

1. No target → `gh pr view --json number,state,isDraft,headRefOid,baseRefName`.
2. Bare number or PR URL → use it.
3. Else `gh pr list --head <target> --json number`; a hit is a branch name.
4. Else it's a path that must exist in the repo: use the current branch's PR
   and scope every subagent to files under that path.
5. Nothing resolves → ask the user.

A PR is required (no bare local diff). No open PR → stop and tell the user to
open one. `CLOSED` → confirm first. `MERGED` → proceed, noting in the report
that the review is retroactive.

## Determine scope

Before spawning anyone, establish what the PR is supposed to do:

1. Read the PR title and description (`gh pr view <n> --json title,body`) and
   the diff; note if the description looks stale.
2. Find the linked Linear ticket (`[A-Z]+-[0-9]+` in branch, title, or body,
   or a Linear URL) and read it with `get_issue`, plus any attached plan
   document (the most precise statement of scope). None findable → say so in
   the report and use the PR description + diff.
3. Write one or two sentences of intended scope. Mismatches (stale
   description, diff not addressing the ticket, diff doing more) go in the
   report as context, not findings.
4. Assess the goal and owning layer below, then pass the scope statement,
   assessment, supporting sources, and open questions to every subagent.

## Assess the goal and owning layer first

Before judging implementation quality, ask: **Is this PR worth pursuing in
its current form, and does it solve the real problem at the layer that owns
the behavior, or locally patch a concern that needs a shared fix?** Apply
this to the linked task as well as the diff; a ticket is evidence of intent,
not proof that its proposed solution is right.

- **Value and outcome:** What concrete user or product scenario needs this?
  What observable outcome makes the task complete? Is the need still present,
  already solved, or superseded? Separate missing evidence from lack of value.
- **Cause and approach:** For a defect, what evidence connects the failure
  to its cause? For a feature, why does this mechanism serve the intended
  outcome? Would a smaller change or existing capability meet the same need?
- **Ownership and reuse:** Is the behavior specific to this adapter, shared
  across adapters, or policy shared across SDKs? Search existing owners and
  affected consumers before accepting another lifecycle, state, retry,
  configuration, or protocol mechanism. Read relevant sibling source when
  shared behavior is implicated; distinguish Python's `src/band/core` from
  `band-sdk-core`. Similar names alone do not establish equivalent contracts.
- **Fit and tradeoffs:** Does the proposed owner have the right lifetime and
  authority? Compare cancellation, failure, recovery, and compatibility
  semantics where relevant. What concrete requirement prevents reuse? Avoid
  both local workarounds and speculative generalization.
- **Scope and cost:** Does the PR bundle an independent bug fix or subsystem
  redesign with a feature? Are the new API, states, configuration, and
  maintenance cost justified? Would narrowing, splitting, or abandoning the
  approach better serve the task?

Record a short assessment: `SUPPORTED`, `RETHINK`, or `UNCERTAIN`, with
source references, the main tradeoff, and any unanswered question. For a
clearly local change, a brief placement rationale is enough. Keep triage
cheap: use targeted source reads, and mark deeper comparisons not checked
rather than running experiments or auditing every adapter. Cross-adapter
similarity is a lead, not a proven shared defect or permission to expand scope.

Use `RETHINK` for an evidence-backed problem with the goal, approach, or
owning layer; use `UNCERTAIN` when missing evidence needs deeper review.
Recommend the smallest next investigation or scope correction. Do not turn
architectural preference into a blocking defect without a concrete consequence.
Ask the user only for a material goal or scope decision the available sources
and existing authorization cannot resolve.

**In scope** = the diff introduced it, changed it, or made it newly reachable
or newly wrong. Proximity isn't scope: a pre-existing issue near a touched
line that the diff didn't create or affect is out of scope for every group.
*Example:* the diff changes `OrderService.submit`'s validation; a defect in
`submit` is in scope, the same missing check in untouched `cancel` is not.
