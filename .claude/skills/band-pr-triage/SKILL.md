---
name: band-pr-triage
description: Use this skill for a quick, cheap, read-only PR triage, also invoked as /band-pr-triage [target] [--level low|medium]. First assess whether the PR and linked task pursue a worthwhile goal at the right owning layer, then inspect readiness signals, the diff, and review history. Returns DRAFT, MERGE, HEAVY REVIEW, CHANGES NEEDED, or RETHINK for an evidence-backed design concern or a nonconverging fix loop. Never posts, fixes, or changes anything. Low adds one scan agent; medium adds Edge-Case and Logical-Bug hypothesis scans, still unproven. Does not call the built-in code-review skill.
argument-hint: "[target] [--level low|medium]"
---

# PR Triage

First assesses whether the PR's goal, approach, and owning layer are worth
pursuing; then decides whether it can merge, needs `band-code-review`, or
needs rethinking. It is not a review. It posts nothing, fixes nothing, and proves nothing; its scans
produce leads that only ever push the verdict toward more review.

Follow [the shared runtime guidance](../workflow-runtime.md) for available
tools, model selection, bounded parallel work, scratch files, and cleanup.

This is a cheap read-only assessment, not the author's readiness review in
`AGENTS.md` "PR Lifecycle and Readiness Review". Do not mark a draft ready or
treat this scan as evidence that planning, implementation, and testing are complete.

## Invocation

`/band-pr-triage [target] [--level low|medium]` (default `low`)

- **target** — PR number, URL, or branch; none means the current branch's PR.
  A path target is treated as its PR, unscoped.
- **low** — signals plus one scan agent over angles A–D and three fixed
  questions, plus the goal and ownership assessment.
- **medium** — low, plus an Edge-Case mapper and a Logical-Bugs explorer that
  return hypotheses only (no skeptic, no proofs).

It does not use the built-in `code-review` skill: that skill reviews the
session's working-directory diff, not a PR.

## Steps

1. **Target and scope.** Follow
   `.claude/skills/band-code-review/pr-target-scope.md`, including its goal
   and owning-layer questions before technical readiness signals. Report
   this assessment even when size or stage prevents a scan. The user's working
   tree is never touched: no checkout, no
   `.claude/skills/band-here-is-a-review/pr-workspace.md` step.
2. **Worktree.** Fetch the PR head, then
   `git worktree add --detach <scratchpad>/triage-<n> <headRefOid>`. Write the
   diff to `<scratchpad>/triage-<n>.diff` with `git diff <merge-base> <headRefOid>`
   (never `gh pr diff`, which refuses past 20,000 lines). Agents read only from the worktree
   and the diff, and never run git checkout/switch/reset/stash/commit.
3. **Signals** (no LLM; `gh` and `git` only):
   - Stage and author readiness: read `isDraft` and inspect the linked evidence
     for the current implementation. Missing material readiness evidence is
     a gap, not proof that the work was never done; report what is missing.
     This repo skips its main CI jobs for drafts, so an expected draft skip
     is not itself a disabled regression test. A failed rollup on a draft
     does not establish that its product tests ran and failed.
   - CI: `gh pr checks <n>`. Failing blocks; pending means not green yet.
   - Size and file kinds from `git diff --numstat <merge-base> <headRefOid>`:
     source, tests, docs, dependencies or lockfiles, CI or release config,
     migrations or schema, generated. Over 1,000 changed lines: skip the scan,
     verdict is HEAVY REVIEW (too large for a cheap read) — unless the PR is a
     **verified relocation**, in which case the scan runs instead.
   - **Verified relocation** — all three hold: the scope step says the PR only
     moves or reorganizes code; `relocation.sh <merge-base> <headRefOid>` (next
     to this file) reports at least 80% of changed lines as moved and test
     function names identical; and no file outside `tests/` and docs has more
     than 20 changed lines. It lifts only the size rule: every other
     HEAVY REVIEW trigger (CI-config change, public API, …) still applies, and
     the moved lines were not read.
   - Source behavior changed with no test change.
   - Added lines that skip or disable a test (`skip`, `xfail`, `ignore`,
     `xit`, …). Read each hit; a removal, a moved line, or an unrelated word
     is not one.
   - Unresolved review threads (`gh api graphql` `reviewThreads`).
   - Scope mismatch noted by the scope step (stale description, diff doing
     more or less than the ticket).
   - **Review history** (resolved threads count too). No-fix records
     (`.claude/skills/band-code-review/no-fix-records.md`) are dropped
     claims, not review rounds: leave them out of these signals and the
     thread digest. Pull the reviews and
     inline comments (`gh api --paginate .../pulls/<n>/reviews` and
     `.../comments`), thread state (`reviewThreads`), and the PR's commits.
     Use [the shared feedback intake](../band-here-is-a-review/pr-feedback.md)
     for pagination and comment identities, retaining resolved history.
     A **round** is a review with inline comments, followed by commits. Signals
     (the numbers are starting points; tune them):
     - **Hotspot** — one file with at least 3 threads.
     - **Recurrence** — a new thread within 20 lines of an earlier one, after
       a commit touched that file; or a comment posted after its thread was
       resolved.
     - **Fix-induced rounds** — new threads on commits made in response to the
       previous round, in two consecutive rounds.
     - **Many rounds** — at least 3.
     Write one line per thread (`file`, `line`, round, resolved, first 200
     characters) to `<scratchpad>/triage-<n>.threads.txt`.
4. **Scan.** Scan agents are read-only. Each gets the worktree path, the diff
   path, the scope statement, the goal and ownership assessment, and
   `.claude/skills/band-code-review/angle-groups.md` with its group letters.
   - **low** — one agent inheriting the session model skims A–D for what the diff
     itself shows, and answers: does it do what the scope says; is there a
     plain blocking defect; does it touch stateful, concurrent, lifecycle, or
     protocol logic; and, from the thread digest, do the earlier comments
     share one root concern. Challenge the goal and ownership assessment:
     does the diff support the claimed need and placement, or reveal an
     existing owner, a local workaround, or unjustified scope?
   - **medium** — also the E mapper and F explorer on the session model,
     hypotheses only, at most five each, ranked by consequence. Each gets
     the closed records (`.claude/skills/band-code-review/no-fix-records.md`,
     tested against `headRefOid`),
     and a lead matching one is dropped.
   For a verified relocation, tell every scan agent to read only the lines
   `git diff --color=always --color-moved=zebra --color-moved-ws=allow-indentation-change`
   leaves plain red/green, not the moved ones.
   Only the A–D scan agent gets the thread digest. Every agent returns a
   short structured list (`file`, `line`, `severity`, one-sentence `body`) and
   nothing else. Treat every item as an unverified lead.
5. **Verdict**, taking the first row that matches:
   1. **RETHINK** — the goal and ownership assessment found an evidence-backed
      reason to reconsider the task, approach, or owning layer; or there are
      fix-induced rounds, or any two of the other history signals. State the
      concrete concern, sources, and consequence. For history signals, name
      hotspot files, thread counts, and the shared concern, marked as an
      unverified lead. Do not recommend `band-code-review --fix`; recommend
      the smallest design investigation or scope correction. Still list
      CHANGES NEEDED blockers and draft status where applicable.
   2. **DRAFT** — the PR is a draft. List any blockers and scan leads, and
      identify remaining author readiness work; do not issue a MERGE verdict.
      Early feedback is allowed, but promotion requires the author's review.
   3. **CHANGES NEEDED** — failing CI, an added skipped or disabled test, or
      a scan item you confirm yourself by reading the cited lines to be a
      plain defect. List each with `file:line`. Never confirm by running a
      repro; that is `band-code-review`'s skeptic.
   4. **HEAVY REVIEW** — any of: touches stateful, concurrent, lifecycle, or
      protocol logic; public API or signature change; dependency, migration,
      or CI-config change; a scan `issue:` or hypothesis you didn't confirm;
      unresolved threads; scope mismatch; source change without tests; any
      material author-readiness evidence missing; an UNCERTAIN goal or
      ownership assessment; or other doubt. Name the
      angle groups warranted (E and F for lifecycle or
      any hypothesis, else A–D) and end with `/band-code-review <n>`.
   5. **MERGE** — every signal clear and the scans returned nothing beyond
      nits (list them as non-blocking). For a requested CI diagnosis, use
      `band-ci-triage` rather than reproducing failures during readiness triage.
      With CI pending: "MERGE once CI is
      green".
6. **Cleanup.** Await every scan worker, or stop it through the host's agent
   controls using its recorded ID and confirm it stopped. Only then remove
   this run's worktree with
   `git worktree remove --force <scratchpad>/triage-<n>` and delete its diff
   and thread-digest files. Follow the shared runtime guidance; never kill
   processes by name. Record any cleanup failure in the final report, keeping
   resources still in use.

## Output

At most ten lines: the verdict; a short goal and owning-layer assessment;
the reasons, one line each with `file:line`
where there is one; one `Not checked:` line; the next command if any.

`Not checked:` is always present: at low, flow and logic bugs weren't
scanned; at medium, they were scanned but not proven; security is outside
both this skill and `band-code-review`, so say so when sensitive paths
(auth, crypto, secrets, permissions) changed. For a verified relocation, add
that moved lines were matched, not read. A MERGE is a statement about
what was checked, not an approval.

A RETHINK ends with relevant options, not an automatic fix command: clarify
the outcome, narrow or split the PR, reuse an existing owner, investigate a
specific shared gap, or drop superseded work. Recommend one with evidence;
do not prescribe a redesign or model-based test for every concern. Triage
remains read-only; ask for a decision only when the next action needs one.
