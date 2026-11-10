---
name: band-pr-assess
description: >-
  Assess a PR's goal, design fit, and scope, file follow-up tickets when needed,
  then invoke band-code-review --fix once (default cycles) before any
  approve/merge when headed toward ship. Use when the user asks to assess a PR
  (including typos like "asses this pr"), says "is this PR good", "evaluate this
  PR", "quality-check this PR", /band-pr-assess, or asks whether a change is the
  right cross-system design before shipping. Not a substitute for band-pr-triage
  alone; merge is forbidden until that --fix run has converged (skip --fix on
  wrong-layer, incomplete-claim, or design-only stops).
argument-hint: "[target]"
---

# PR Assess

End-to-end path from "what is this PR?" to merge-ready: design/scope judgment,
follow-ups, then the mandatory `band-code-review --fix` gate when headed
toward ship. It is not a read-only triage (`band-pr-triage`) and not a
review-only pass (`band-code-review` without `--fix`).

Follow [workflow-runtime.md](../workflow-runtime.md) for tools, delegation,
authorization, and cleanup. Finishing this skill does not mark a draft ready,
establish peer approval, or waive required CI/merge-queue checks.

## Invocation

`/band-pr-assess [target]`

- **target** — PR number, URL, or branch; none means the current branch's PR.

## Ship intent

**Headed toward ship** when the user asked to assess toward merge, or to
approve / enable auto-merge / merge / queue / ship. A design-only or
scope-only ask (right seam? keep narrow?) is **not** headed toward ship.

## Hard gate

When headed toward ship, **never** approve, enable auto-merge, merge, queue,
or recommend MERGE until all of the following hold:

1. `band-code-review --fix <target>` was invoked **once** (default
   `--cycles`) and that run **converged** (see
   [cycles.md](../band-code-review/cycles.md): zero verified in-scope
   `issue:` findings; nits/suggestions fixed in the converging round).
2. Every proven out-of-scope item has a Linear ticket link or an explicit
   leave-open decision recorded in this run's output (a pre-review "don't
   file tickets" answer is **not** leave-open for later OOS findings).
3. Author readiness against `AGENTS.md` "PR Lifecycle and Readiness Review"
   passes; otherwise keep draft and do not promote or merge.
4. CI / merge-queue required checks allow the ship action.

Do **not** re-invoke `--fix` after capped or not-converging to force green.
Those outcomes are not success: report the signal, keep threads open per
that skill, and stop for a user decision (design fix / rethink / stop).
"Review finished" or "last cycle's fixes pushed" is not convergence.

If push access is missing and `band-code-review` strips `--fix` to
verdict-only, the gate is **blocked** (not converged) — do not ship.

Wrong-layer, incomplete-claim, and design-only stops (below) end the run
**before** this gate; do not run `--fix` or ship to paper over them. A
design-only ask may still run `--fix` when the user explicitly asks for
review/fix in the same turn.

## Steps

### 1. Target, scope, workspace

Follow [pr-target-scope.md](../band-code-review/pr-target-scope.md) and
prepare the checkout per
[pr-workspace.md](../band-here-is-a-review/pr-workspace.md). Write 2–4
sentences: problem, what ships, what is explicitly out of scope. Note
missing/stale ticket linkage. Carry that scope statement through later
steps.

When headed toward ship and step 1 finds no push route for fixes, stop with
`band-code-review --fix`: blocked (no push) — do not pretend a later
verdict-only review is convergence.

### 2. Design fit

Judge whether this is the right seam for the claimed change:

- **Ownership** — which layer owns the behavior (e.g. runtime vs adapter vs
  platform vs host; or skill/guidance vs product code vs CI)
- **Consistency** — matches existing optional-capability, API, or workflow
  patterns in this repo
- **Completeness claim** — honest vertical slice (foundation) vs incomplete
  coverage sold as complete

Verdict, one concrete reason each: **right layer** / **right first cut** /
**wrong layer** / **incomplete claim**.

- **Wrong layer** — report; ask/file a correct-seam follow-up (step 4 mini)
  unless the user declines; then stop. Do not run step 5 or ship.
- **Incomplete claim** — the PR sells completeness it lacks. Require a scope
  rewrite in the PR, expand-here completion before ship, or explicit gap
  tickets plus user accept of the known gaps. Do not treat this as
  **right first cut**. Stop or stay in follow-ups until that disposition
  exists; do not run step 5 toward ship until resolved.
- **Right first cut** — honest foundation scope only (gaps named and ticketed
  or accepted as follow-ups).
- **Right layer** — seam and completeness claim both hold.

### 3. Scope call

Default: keep a focused vertical slice in this PR. Broader backends, platform
wire paths, sibling adapters, or adjacent skills are **separate work** unless
omitting them makes this change incorrect.

State clearly: stay narrow vs expand here (and why). If the PR is part of a
stack, note that and follow [band-stacked-prs](../band-stacked-prs/SKILL.md)
only when stack mechanics matter for merge order — that does not waive this
skill's hard gate for the assess target.

### 4. Follow-ups

When scope, design, or review leaves real gaps (including proven out-of-scope
defects and wrong-layer redesigns):

- Prefer the draft/confirm/create gate in
  [band-here-is-a-review](../band-here-is-a-review/SKILL.md) (Decision gate —
  out of scope): draft, confirm, then `save_issue`
- Default team Integrations and assignee `me` unless the user names another
- Shape: Context / Do / Depends / Not this ticket — informative, no fluff
- Link the PR; relate sibling tickets
- Do not expand this PR to absorb those tickets unless step 3 said to

Ask before filing if the user has not already asked for tickets. Re-ask for
any **new** proven OOS items after step 5; an earlier "don't file" does not
cover them.

### 5. Required review/fix (must converge)

Run this step only when headed toward ship (or the user explicitly asked for
review/fix), and only if step 2 left no unresolved **wrong layer** or
**incomplete claim** disposition.

Run [band-code-review](../band-code-review/SKILL.md) with **`--fix`** on the
same target **once**. Follow that skill fully (workspace, simplify, cycles,
root-cause fixes, thread close-out). Do not substitute a lighter scan or
triage verdict.

Use that single run's outcome:

- **Converged** → continue to step 6 only if ship was asked
- **Capped** / **not converging** → halt for the user per that skill (its
  in-run Wait/resume still applies inside that one invocation)
- **`--fix` stripped** / verdict-only → **blocked**; do not ship

Out-of-scope findings: disposition via step 4 (ticket or explicit leave-open).
Deferred items need a ticket link before merge.

For a **design-only** ask: stop after steps 1–4 (and any wrong-layer /
incomplete-claim disposition). Report that `--fix` is still required before
ship; do not mutate the PR with `--fix` unless the user explicitly asks for
review/fix.

### 6. Ship

Only when headed toward ship, step 5 **converged**, hard-gate items 2–4 hold,
and the user asked to ship:

- Summarize goal, design verdict, scope call, tickets filed, review/fix
  outcome (converged; cycles run)
- Approve and merge (or queue) only when CI/merge-queue gates allow — never
  bypass required checks

If the user did **not** ask to ship: end with `Merge: not yet` and do **not**
recommend MERGE even if review/fix converged.

## Output

Short sections matching steps 1–6. No restated PR body. End with:

- Tickets filed (links) or none; leave-open decisions (if any)
- `band-code-review --fix`: converged (cycles N) / capped / not converging /
  skipped (wrong layer | incomplete claim | design-only) / blocked (why)
- Merge: not yet / queued / merged (link) — never recommend MERGE without a
  ship ask

## Not this skill

- Cheap merge/heavy-review signal only → `band-pr-triage`
- Review without the assess/follow-up framing, or without `--fix` when the
  user only asked for a review pass → `band-code-review` as invoked
- Security-only → `band-security-review`
