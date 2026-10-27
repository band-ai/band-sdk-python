---
name: band-code-review
description: Run the team's six-angle review of a PR or branch only when a user explicitly asks for it; never on Claude's own initiative. Verify findings and existing feedback, publish one review, and optionally simplify and fix root causes with --fix for up to --cycles N rounds. Use band-here-is-a-review for supplied feedback alone.
argument-hint: "[target] [--fix] [--cycles N]"
---

# Code Review

Read [workflow-runtime.md](../workflow-runtime.md) for runtime capabilities,
delegation limits, authorization, and cleanup. Apply the same review stages
serially when delegation is unavailable, and disclose the loss of independence.
In that fallback the coordinator performs each role in turn, retaining its
verification requirements and phase boundaries.

The team's fixed-criteria review of an open PR. Four lenses (A–D) scan the
diff in parallel while Edge-Case Flow Analysis and Logical Bugs run a staged
investigation. The coordinator posts verified findings as one batch of inline
comments. The PR's existing open feedback (anyone's) always goes through
`band-here-is-a-review` for a verdict. Triage runs once before cycle 1.
With `--fix`, a `simplify` pass follows triage, then
`band-here-is-a-review` applies root-cause fixes and replies on and
resolves every thread.

Every fix targets the root cause. A wrapper, special case, or try/except over
the symptom is a bolt-on and doesn't count.

## Invocation

`/band-code-review [target] [--fix] [--cycles N]`

- **target** — PR number, PR URL, branch name, or path. None: the PR open for
  the current branch.
- **--fix** — run `simplify` once before cycle 1, and fix findings in every
  cycle (see "Fixing"). Without it nothing changes in code: one review is
  posted and existing open feedback gets a verdict-only close-out.
- **--cycles N** (default 3 with `--fix`, ignored without) — max cycles. A
  cycle is one full-PR review plus its fix round. `simplify` is not a cycle.

## Resolve the target and scope

Draft PRs may be reviewed for early feedback. Assess author readiness against
`AGENTS.md` "PR Lifecycle and Readiness Review"; finishing this skill does not
automatically promote a PR, establish mergeability, or waive missing validation.

Follow [pr-target-scope.md](pr-target-scope.md): it resolves the
target and PR state, then writes the scope statement every subagent gets.
Apply its goal and owning-layer assessment before workspace mutation or
`simplify`. If it returns `RETHINK`, report the evidence and recommended scope
or design correction before entering the fix loop. Continue read-only review
where useful, but defer fixes that depend on an unresolved goal or architecture
decision. Existing authorization for a concrete correction still applies.

## Initial triage (once, before cycle 1)

Run [band-pr-triage](../band-pr-triage/SKILL.md) at its default `low` level
on the resolved PR before preparing a mutable workspace, running `simplify`,
or starting cycle 1. This applies with and without `--fix`. Record its head
SHA, verdict, goal and ownership assessment, and unverified leads. Reuse the
scope evidence already collected; do not repeat source reads unnecessarily.

- `RETHINK`: report the concern and recommended next step first. Continue
  read-only investigation where useful; defer changes that depend on an
  unresolved goal or architecture decision.
- Other verdicts: continue the explicitly requested full review. `MERGE`
  does not skip it; `DRAFT` does not promote the PR. Carry blockers and leads
  into the appropriate angle groups for verification.

Triage's suggested review command is routing advice, not a recursive skill
invocation. Do not call `band-code-review` again from this preflight. Triage
is not a review cycle and does not consume `--cycles`. Run it once per review
run, not before later cycles or when resuming the same run. If the head changes,
refresh affected evidence rather than treating the earlier verdict as current.

After triage, prepare the workspace per
`.claude/skills/band-here-is-a-review/pr-workspace.md` (repo, PR head in sync,
and with `--fix` push access). `--fix` without push access runs as if absent
(no `simplify`, one review, verdict-only close-out) and says so up front.

## Simplify (`--fix` only)

Once, before cycle 1, use the available `simplify` skill on the PR diff
against its base (or the path), passing the scope statement. If unavailable,
perform a local pass for reuse, clarity, and appropriate layering without
changing behavior. This pass never uses a cycle.

1. Select the applicable checks from AGENTS.md "Pre-Commit Checklist"
   for the actual diff and the focused validation guidance in
   `band-here-is-a-review` Phase 5, then run them quietly: use
   the tools' quiet flags (e.g. `pytest -q`), redirect output to a scratchpad
   file, and read only the failures and the summary line. Every gate run in
   this skill, including the fix rounds', is quiet. Revert any change that
   turns it red; don't patch it.
2. Commit the surviving cleanup separately (`refactor:`) and push. Cycle 1
   starts from that commit.

## The six angle-groups

Defined in [angle-groups.md](angle-groups.md) (A Style &
Hygiene, B Design & Reuse, C Runtime Safety & Observability, D Test Quality,
E Edge-Case Flow Analysis, F Logical Bugs). Every finding has one owning
group; each agent reads the file's intro and only its own group's section.

## Distribution

Follow [distribution.md](distribution.md) for the cycle digest, reviewer
briefs, evidence requirements, and role ownership. Read it before assigning
review work.

## Comment bar

Factual, not narration: what the code does and the concrete consequence,
nothing visible from the line itself. Prefix by severity: `issue:` (real
defect, or a Test Quality/Runtime Safety violation), `nit:` (style, hygiene,
reuse; non-blocking), `suggestion:` (worth considering). A finding recurring
across files is **one** comment naming the other `file:line` sites. A
skeptic finding marked `out_of_scope` is prefixed `issue (out of scope):`,
says in one sentence why it's outside the scope, and never blocks.

## Posting mechanics

After verifying findings, follow [publishing.md](publishing.md) to publish
one review at the exact head and resolve any new no-fix records.

## Fixing

After posting, run `band-here-is-a-review` as a distinct verification and fix
stage, preferably in a resumable worker. Keep its handle and the phase result;
if resumption is unavailable, pass that result to a new worker, or continue
serially with the limitation disclosed. The coordinator does not override the
stage's verified verdicts. With no unresolved feedback, no out-of-scope
finding, and either no `--fix` or no findings, skip this stage; under `--fix`
that cycle is converged.

Workers return user decisions to the coordinator. It checks existing
authorization, asks only for a missing decision, and relays the answer.

1. **Package the batch.** Every **unresolved** digest item (thread, author,
   whole thread), every out-of-scope finding this review posted, plus —
   under `--fix` only — its other findings, as numbered items (`group`,
   `file`, `line`, `severity`, `body`, `fix_note`, `proof`, its thread).
   Without `--fix` the review's in-scope findings are left as posted. Pass
   the scope statement. The in-scope findings are in scope by construction.
   Existing feedback goes through the normal scope gate (fix now / followup
   ticket / drop, asking the user once). Out-of-scope findings go through
   the same gate, marked proven out of scope, so the user is offered only
   fix now or a followup ticket; without `--fix`, fix now leaves the thread
   open as the record.
2. **Verify (its Phase 2), by tier.** Each item gets a verdict (CONFIRMED /
   PARTIALLY RIGHT / WRONG / NOT WORTH IT). This review's own findings were
   already verified at the head SHA by their group or the skeptic, so tell it
   to verify them no deeper than their tier:
   - **E/F findings** — re-run the attached `proof`; a proof that no longer
     reproduces makes the finding WRONG. Re-check reachability only if
     commits since the proof touched its caller chain.
   - **A–D `issue:`** — full verification against the real code.
   - **A–D `nit:` / `suggestion:`** — confirm the cited line still matches
     the quoted code at head; verdict CONFIRMED.
   - **Existing feedback** — full verification, always.
3. **Pause after its Phase 3, then always send the decision.** Under `--fix`
   the first pause is never the end of the run. Enter verified items in the
   ledger and decide using [cycles.md](cycles.md); without `--fix` there is no ledger and
   no decision, only the verdict-only close-out. Give the fix stage exactly one of:
   - **Fix** (`--fix`, continuing or converged, every cycle including the
     last) → proceed to Phase 4. On converged it fixes only nits and
     suggestions. Wait for its verified diff and proposed thread replies.
   - **Verdict-only close-out** (no `--fix`, or not converging) → no code
     changes: reply on every item with its verdict; resolve WRONG, NOT WORTH
     IT, deferred (with the followup link), and answered-question items;
     leave CONFIRMED and PARTIALLY RIGHT threads open and unfixed as the
     record. The report lists them.
4. **Fix (its Phases 4–5).** Root-cause plans (items sharing a root cause
   share one fix), implementation, and the applicable validation checks. Keep
   each fix round scoped to checks affected by that round; do not rerun a broad
   suite just because another cycle began. The coordinator reviews the result,
   commits, and pushes before closing any fixed thread.
   A failing-test proof must turn green; a repro must stop
   reproducing and gets a regression test when feasible. Its
   architectural-change gate applies.
5. **Close the loop (its Phase 6).** The coordinator publishes the verified
   verdicts and resolves fixed or declined threads. Confirmed defects whose
   fixes were not published stay open, including in verdict-only mode.

## Cycles

With `--fix`, read [cycles.md](cycles.md) before the first cycle. It defines
the ledger, convergence decision, cap, and design escalation; preserve those
decisions when scheduling subsequent work. Without `--fix`, run one cycle.

## Cleanup

Runs last on every exit path (normal end, converged, capped, halted, user
stop, error), before the final report. Follow the runtime guide to stop and
confirm completion of this run's tracked workers and background processes.
Never wait indefinitely on a silent worker; report incomplete work and anything
that could not be stopped. Delete this run's `pr-<n>-cycle-<k>.diff` files.

## Final report

Short and nothing else (no restated comment bodies, no process narration):
- PR link; the scope statement, plus any scope mismatch or missing-ticket
  note.
- Goal and owning-layer assessment, its evidence, and any unresolved design
  decision; lead with this when the approach needs rethinking.
- Initial triage verdict and the head assessed.
- Comment counts by group and severity, totalled across cycles.
- Existing feedback handled: item count with verdict counts.
- Out-of-scope findings, one `file:line` each: fixed now, left open, or
  deferred with the ticket link.
- No-fix records: how many were posted, and how many closed records were
  skipped.
- With `--fix`: what `simplify` changed; items fixed vs. declined.
- Each thread left open by a verdict-only close-out, one `file:line` each.
- Cycles run (`simplify` not counted) and how they ended: converged (with
  the last cycle's nit fixes unreviewed), capped (last fixes unreviewed), or
  not converging with the signal and the user's decision. On non-convergence,
  lead with the design review and note the open threads.
- The last cycle's Edge-Case coverage map `not examined` flows, one line
  each.
- Any agent or process cleanup could not stop, or had to stop because it went
  silent.
- If the PR was already merged, one line saying so.
