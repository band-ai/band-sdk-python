---
name: band-here-is-a-review
description: Handle supplied code-review feedback, pasted findings, or a PR review link. Verify each claim, reject unsupported findings, fix confirmed in-scope root causes, and answer the review threads. Use for "address this review" or "assess and fix these findings"; use band-code-review to review code from scratch.
---

# Here Is a Review

Read [workflow-runtime.md](../workflow-runtime.md) for available tools,
delegation, and existing authorization. When called as a worker, return all
remote publication actions to the coordinator, including pushes and replies.

The user will provide a code review. Your job is NOT to implement it. Your job is to
**assess it, verify it, and fix only what genuinely deserves fixing — cleanly.**

Core stance: the review is a set of *claims by a fallible reviewer*, not a task list.
Never implement before verifying. Never get defensive. No performative agreement
("You're absolutely right!") — restate the claim, check the code, and respond with
evidence.

## Phase 1 — Intake

1. Collect the review. If it lives on a PR (a PR link or number, or "the review on
   my PR"), fetch it yourself — see "Fetching a PR review" below. Otherwise use the
   pasted text.
2. Split the review into discrete, numbered findings. One claim = one finding, even
   if the review bundles them. A PR finding keeps the thread or comment it came
   from, so Phase 6 can answer it. A comment that asks a question rather than
   claiming a defect is answered in Phase 6, with no code change unless the
   answer uncovers a real defect.
3. For each finding, capture: the claimed defect, the file/line it points at, and the
   fix the reviewer suggested (if any). The suggested fix is a hint, not the plan.
4. Identify what code the review is about (current diff, a branch, a PR, specific
   files). If genuinely ambiguous, ask before proceeding.
5. Pull the linked Linear ticket (from the branch name, PR title, or PR description)
   with its acceptance criteria and any attached plan document, and read it with the
   PR title/description and `git diff <base>...HEAD --stat` (the PR's base branch).
   The ticket says what the task is meant to do; the PR description says what this
   PR claims to do.
6. From those, state the task's scope in one sentence. That sentence is the yardstick
   for Phase 3. If they don't yield a scope, ask the user; don't assume the whole repo
   is fair game. While reading, check two things:
   - **Description freshness**: does the PR description still match what the diff
     does now, or is it stale (scope grew/shrank since it was written)?
   - **Ticket coverage**: does the diff actually address the ticket's stated
     problem/acceptance criteria, or does it drift from it?
   Note any mismatch in the Phase 7 report — it's not a "finding" to fix, it's
   context the PR's reviewer needs. Don't block on it or nag the user; just report it.

### Fetching a PR review

- First prepare the workspace per [pr-workspace.md](pr-workspace.md): the right repo,
  the PR head checked out and in sync, and whether fixes can be pushed.
  Without push access, use verdict-only mode throughout: verify and propose
  fixes, skip Phases 4–5, and leave confirmed unfixed findings open in Phase 6.
- Read [pr-feedback.md](pr-feedback.md) for thread pagination, non-thread
  findings, settled dispositions, and no-fix records. Keep the full discussion
  with each finding when assigning verdicts.

### Called with structured findings

When another skill (e.g. `band-code-review`) hands over numbered findings with a scope
statement, reuse that statement and skip Phase 1 steps 5–6 and their Phase 7 check.
A finding with an attached `proof` is verified in Phase 2 by re-running that proof, not
by re-deriving the failure; re-check its reachability claim only if commits since the proof
touched the caller chain. The caller may mark its own findings as already verified at the
head SHA and name a lighter tier (e.g. confirm the cited line still matches): do exactly
that and no more. A finding the caller marks proven out of scope is real, so
the Phase 3 decision gate offers it only a followup ticket or fix now, never
drop. Feedback from anyone else always gets full verification. Findings that carry a PR
thread are closed in Phase 6 like any other PR review. If the caller asks to pause
after Phase 3, stop there and return each finding's verdict; continue from Phase 4
only when told to. If it then asks for a **verdict-only close-out**, skip Phases 4–5
and run Phase 6 without code changes: reply on every thread with its verdict, resolve
the WRONG, NOT WORTH IT, deferred, and answered-question ones, and leave CONFIRMED
and PARTIALLY RIGHT threads open — the reply states the verdict and the proposed
fix, and the open thread is the record that it isn't done.

## Phase 2 — Verify every finding against the real code

Verify against the **actual code**, never the review's excerpts — reviewers quote
stale or truncated snippets. For each finding:

- Read the code it targets, plus enough surrounding context (callers, tests, the
  module's conventions) to judge it.
- For claimed bugs: construct the concrete failure scenario — what input/state makes
  it go wrong? If you can cheaply prove it with a quick script, failing test, or live
  probe, do so. A bug you can't articulate a failure scenario for is not confirmed.
- Check whether the code already handles the case (guard elsewhere, invariant upheld
  by the caller, existing test covering it).
- For independent findings, verification can be fanned out to parallel subagents;
  scrutinize their reports before accepting.

Assign each finding a verdict:

| Verdict | Meaning |
|---|---|
| **CONFIRMED** | Real defect, failure scenario articulated (ideally reproduced) |
| **PARTIALLY RIGHT** | Reviewer smelled something real but misdiagnosed it — record the *actual* problem |
| **WRONG** | Claim doesn't hold against the code — record the evidence |
| **NOT WORTH IT** | Technically true but irrelevant: taste-only, dead path, cost exceeds benefit |

## Phase 3 — Relevance and scope gate

Two axes: does the finding matter, and is it in the current task's scope.

### Does it matter

Only CONFIRMED and PARTIALLY-RIGHT findings that **matter** proceed to fixing.
"Matters" means: user-visible breakage, data corruption, security exposure, a real
maintenance trap, or a violated project invariant or rule (`AGENTS.md` and the
guideline skills `band-python-conventions` and `band-python-test-conventions` — e.g. duplicated logic,
magic literals, narrating comments, tests that assert plumbing or assumptions,
patch internals, wait out wall-clock grace, branch on scenario inside the test,
or treat line coverage as branch coverage). Filter out:

- Pedantry and taste conflicts with the project's established conventions
  (the codebase's existing idiom wins over the reviewer's preference).
- Hypotheticals no real caller, event source, or input can reach.
- Suggestions that would make the code worse-fitting even if locally "cleaner".

Everything WRONG, NOT WORTH IT, or filtered out here is **rejected**: push back with
technical reasoning in the report and, for a PR review, in the thread reply. That's a
first-class outcome of this skill, not a failure.

### Is it in scope

**In scope**: the diff introduced it, changed it, or made it reachable/wrong, or the
ticket's acceptance criteria require it and the diff falls short. Proximity isn't scope —
a line the diff touched is in scope; a pre-existing bug that merely lives in the same
function is not.

**Small** fix = localized, fits files the diff already touches, no API or design-contract
change, guarded by an existing test or one small new one. **Large** = anything else.

- In scope → Phase 4 as usual.
- Out of scope + small, on lines the diff touched → fix it, and mark it in the
  report as an out-of-scope extra so the PR's reviewer knows why it's in the diff.
- Any other out-of-scope finding → the decision gate below. A followup ticket is often
  the right answer: the finding is real, but this PR stays focused on its ticket.

Never silently expand the task's scope, and never silently drop a real finding.

### Decision gate — out of scope

Ask once, covering every out-of-scope finding not already authorized. Each gets one
line (defect + failure scenario + why it's out of scope) and three options: open a
followup Linear ticket (recommended unless the fix is trivial) / fix now and accept the
PR growth / drop. A dropped finding keeps its reason for the reply and report.

For each ticket chosen, do the Phase 4 root-cause thinking *first* — a ticket that only
restates the reviewer's wording is worthless — then **draft, confirm, create**. The draft
carries the failure scenario, `file:line`, the fix you would have written, why it was out
of scope for this task, and a link to the branch/PR; propose the target team/project
alongside it. On the user's OK, file it with `save_issue` (Linear MCP) and report the URL.

The followup's issue ID belongs in your report, the PR description (Phase 6 adds it),
and the Phase 6 reply — never in a code comment or `TODO`.

## Phase 4 — Plan clean fixes (no bolt-ons)

For each finding that survived, plan the fix **at the root cause**:

- Fix the design, don't work around it. If the defect exists because a component's
  design is wrong, the fix changes the design — not a special case, flag, wrapper,
  or try/except slapped on top at the call site.
- Prefer the fix that *deletes or simplifies* over the one that adds. If the clean
  fix and the reviewer's suggested fix differ, take the clean one and say why.
- One coherent change per root cause: if three findings share a root cause, that's
  one fix, not three patches.
- Fit the surrounding code's idiom, naming, and structure so the diff reads like it
  was always there.
- Decide how each fix will be verified: an existing test, a new test that fails
  before and passes after (preferred for confirmed bugs), or a live probe.

A fix triaged "small" in Phase 3 that turns out large once actually planned goes back
through the decision gate rather than quietly landing.

If any planned fix is architecturally significant (changes a public API, a design
contract, or crosses module boundaries in a new way), present the plan and get the
user's confirmation before implementing. Routine fixes proceed without asking.

## Phase 5 — Fix and verify

1. Implement the planned fixes, root cause first.
2. For each CONFIRMED or PARTIALLY RIGHT bug, make the regression test the guard: it
   fails on the old code, passes on the new.
3. Select and run the applicable checks from AGENTS.md "Pre-Commit Checklist".
   Prose-only edits do not require the SDK unit suite. Executable helpers are
   code and need behavioral checks. Run checks quietly and distinguish passed,
   failed, blocked, and not-run checks in the report.
4. Re-check each fixed finding against its original failure scenario.
   If the fixes materially change the design, scope, or tested behavior,
   reassess author readiness under `AGENTS.md` "PR Lifecycle and Readiness
   Review" before any authorized draft-to-ready transition.
5. For a PR review, commit the fixes on the PR branch following AGENTS.md "Commit
   Messages", then push. A delegated worker returns its verified diff and proposed
   replies for the coordinator to commit and publish. Without push access, use
   verdict-only mode from intake: no edits or commits, and confirmed defects remain
   open. Every fixed reply cites the published commit that holds its fix, so if
   the verification gate or the push fails, stop and report instead of starting
   Phase 6.

## Phase 6 — Close the loop on the PR (PR reviews only)

Answer every finding where the reviewer left it. Resolve published fixes,
rejections, deferrals, drops, and answered questions; confirmed or partially-right
defects without a published fix stay open. In a delegated run the coordinator
performs these remote actions from the worker's verified verdicts.

- Re-fetch thread state first and skip any thread someone resolved meanwhile.
- **Reply** once per thread, covering every finding from it, with the
  `addPullRequestReviewThreadReply` GraphQL mutation (the thread `id`). Keep it
  short and factual. No performative agreement.
  - fixed → what changed and the commit SHA;
  - rejected → the verdict and the evidence;
  - deferred → "real, but out of scope for <scope sentence> — tracked in <link>";
  - dropped → that it's real but out of scope, and the reason it won't be done;
  - confirmed but unfixed → the verdict and proposed fix; leave the thread open;
  - a question → its answer.
- **Resolve** eligible threads with the `resolveReviewThread` GraphQL mutation.
- A finding from a review body or a top-level comment has no thread to resolve:
  answer it in one top-level PR comment that quotes or links each finding.
- If any finding was deferred, add its followup link to the PR description
  (`gh pr edit --body`), keeping the format AGENTS.md "Pull Request
  Descriptions" sets.

## Phase 7 — Report

End with a per-finding table: finding → verdict → scope (in scope / out-of-scope-fixed /
out-of-scope-deferred with the issue link / out-of-scope-dropped) → action (fixed /
rejected — why / deferred / dropped — why / proposed but unfixed / answered, for a question). Follow it with a
short summary of what changed and how it was verified. For a PR review, state the pushed
commit(s), which threads were resolved, and which remain open with unfixed defects.
List actionable non-thread findings separately; they have no resolution flag.

A deferred finding is filed, not fixed — say so. Report honestly: failed gates, skipped
items, and pushed-back findings are stated plainly, not buried.

Include the Phase 1 step 6 check: whether the PR description is stale and whether the
diff addresses the linked Linear ticket, flagging any mismatch found.
