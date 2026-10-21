---
name: band-code-review
description: Run the team's six-angle review of a PR or branch when explicitly requested. Verify findings and existing feedback, publish one review, and optionally simplify and fix root causes with --fix for up to --cycles N rounds. Use band-here-is-a-review for supplied feedback alone.
argument-hint: "[target] [--fix] [--cycles N]"
disable-model-invocation: true
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
`band-here-is-a-review` for a verdict. With `--fix`, a `simplify` pass runs
first, then `band-here-is-a-review` applies root-cause fixes and replies on and
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

Follow [pr-target-scope.md](pr-target-scope.md): it resolves the
target and PR state, then writes the scope statement every subagent gets.

Then prepare the workspace per
`.claude/skills/band-here-is-a-review/pr-workspace.md` (repo, PR head in sync,
and with `--fix` push access). `--fix` without push access runs as if absent
(no `simplify`, one review, verdict-only close-out) and says so up front.

## Simplify (`--fix` only)

Once, before cycle 1, use the available `simplify` skill on the PR diff
against its base (or the path), passing the scope statement. If unavailable,
perform a local pass for reuse, clarity, and appropriate layering without
changing behavior. This pass never uses a cycle.

1. Run the repo's full verification gate (per AGENTS.md "Pre-Commit
   Checklist" / CI), quietly: use
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

1. **Digest, once per cycle.** First confirm the checkout still equals the
   PR's `headRefOid` (fix rounds push). Pull `gh pr diff <n>`, inline comments
   (`gh api --paginate repos/{o}/{r}/pulls/{n}/comments`), top-level comments
   (`.../issues/{n}/comments`), review bodies (`.../pulls/{n}/reviews`),
   and thread resolution via `gh api graphql`
   `reviewThreads` (walk `pageInfo`; REST has no resolved state). First pick
   out the no-fix records ([no-fix-records.md](no-fix-records.md))
   and keep the **closed records**; reopened ones leave the digest. Split the
   rest into: **unresolved** (open threads, plus review-body and top-level
   findings still open under `band-here-is-a-review`'s "Fetching a PR review"
   rule), **settled** (resolved as rejected/deferred/dropped), **fixed**. The digest
   stays with the coordinator; reviewers never see it. Its unresolved items
   are what "Fixing" hands over. Write the diff (or path subset) once to a
   scratchpad file, `pr-<n>-cycle-<k>.diff`.
2. Delegate groups A–D, the mapper, and the explorer within the runtime's
   concurrency limits, scheduling additional work as slots free. Pass **paths,
   not content**: each agent gets the diff file's path, the head SHA, the
   scope statement, and the path of `angle-groups.md` with its own group
   letters (it reads only those sections), and reads the files it needs
   from the checkout (in sync with the head, per the workspace check) for
   real context and line numbers. The mapper and explorer also look up
   callers and existing tests themselves. No comment history, fix diffs, or
   earlier findings — they pull attention away from a fresh look. The one
   exception: the mapper and explorer get the closed records, in the form
   `no-fix-records.md` gives.
   - Inherit the session model; do not assume vendor-specific model aliases.
   - **Small PR** (at most 150 added plus deleted lines across at most 3
     files, per `gh pr view <n> --json additions,deletions,changedFiles`):
     run A–D as one agent applying all four angle
     sets. Findings keep their owning group. Larger PRs keep four separate
     agents.
3. Groups A–D verify each candidate against the real file at the head SHA
   (real `file`/`line`/`side`, exact code, in scope, any external fact) and
   return `{group, file, line, side, severity, body, fix_note, proof}`.
   Every agent returns only its structured list (empty if none), with `body`
   at most two sentences and no quoted code beyond the cited line. The mapper
   returns hypotheses and the coverage map, not its state table.
4. After the mapper and explorer return, filter their hypotheses against the
   digest (step 5), then spawn the one skeptic with the survivors and the
   scope statement. It returns
   the same structure; its `proof` holds the repro/test code inline, the
   command to run it, and the real source that reaches it. No other agent
   populates `proof`. A proven defect outside the scope comes back as a
   finding marked `out_of_scope`, never as a drop. For every hypothesis it
   drops (not real) it also returns
   `{file, line, side, claim, reason, footprint}` per `no-fix-records.md`,
   anchored on a changed line of the claim's flow.
5. Filter every finding against the digest:
   - matches a **closed record** → drop.
   - matches **unresolved** → drop; "Fixing" handles the open item.
   - matches **settled** → drop unless it has evidence the decision missed,
     or the thread `isOutdated` (its code changed since the decision).
   - matches **fixed** and the problem is really present → keep. If this run
     fixed it (it's in the ledger) it's a **regression**; an older fix that
     didn't hold is a fresh finding.
6. Groups never dedup against each other.
7. Only the coordinator publishes remote changes: pushes, reviews, replies,
   thread resolutions, and ticket updates. Reviewers are read-only; the skeptic
   writes only in the scratchpad. A fix worker may edit its assigned files and
   run verification, then returns the diff, evidence, and proposed replies.
   No worker switches branches or checks out another ref. State the applicable
   role's permissions in each prompt, and never run reviewers over a mutating tree.

## Comment bar

Factual, not narration: what the code does and the concrete consequence,
nothing visible from the line itself. Prefix by severity: `issue:` (real
defect, or a Test Quality/Runtime Safety violation), `nit:` (style, hygiene,
reuse; non-blocking), `suggestion:` (worth considering). A finding recurring
across files is **one** comment naming the other `file:line` sites. A
skeptic finding marked `out_of_scope` is prefixed `issue (out of scope):`,
says in one sentence why it's outside the scope, and never blocks.

## Posting mechanics

- `commit_id` = `gh pr view <n> --json headRefOid`.
- The skeptic's dropped claims are posted as no-fix records, with or without
  `--fix`, in the same review as the findings.
- No findings and no records → post no review; the report says the review
  found nothing.
- Post all inline comments in **one** `POST /repos/{o}/{r}/pulls/{n}/reviews`,
  sent as AGENTS.md "GitHub PR Inline Comments" says, with `commit_id` and a
  `comments` array (`path`, `line`, `side`, body) and no `event` (stays
  `PENDING`). Per-finding calls create separate auto-submitted reviews;
  never do that.
- `side`: `RIGHT` for added/unchanged lines, `LEFT` for deletions;
  multi-line needs `start_line`/`start_side`.
- A `path`/`line` outside GitHub's diff hunks returns `422`: move that
  finding to the review body with an explicit `file:line`. A record moves to
  another changed line of its flow instead, or is left out (the body has no
  thread to resolve).
- Submit once: `POST .../reviews/{id}/events`. Event is `COMMENT`, or
  `REQUEST_CHANGES` if any posted finding is blocking (an in-scope `issue:`
  from C, D, E, or F). GitHub rejects `REQUEST_CHANGES` from the PR's author (compare
  `gh pr view <n> --json author` with `gh api user`): then submit `COMMENT`
  and open the body with "Blocking — would be REQUEST_CHANGES on a PR by
  someone else." Body = short summary plus anything routed out of line
  comments.
- Then resolve each new record thread with the `resolveReviewThread` GraphQL
  mutation. A record that won't resolve is still a record by its marker;
  name it in the report.

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
   ledger and decide (see "Cycles"); without `--fix` there is no ledger and
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
   share one fix), implementation, and the full verification gate. The coordinator
   reviews the result, commits, and pushes before closing any fixed thread.
   A failing-test proof must turn green; a repro must stop
   reproducing and gets a regression test when feasible. Its
   architectural-change gate applies.
5. **Close the loop (its Phase 6).** The coordinator publishes the verified
   verdicts and resolves fixed or declined threads. Confirmed defects whose
   fixes were not published stay open, including in verdict-only mode.

## Cycles

One cycle = one full-PR review with every group, its verification, and its
fix round. Nothing needs an extra cycle to get fixed, the last cycle's
findings included. Fresh cycles shift focus and surface missed issues, so
later cycles never narrow to the fix area; closed no-fix records are the
only history their agents see. "Resolve the target and scope" and "Simplify" run once, up front. Without `--fix`, exactly one
review runs, then the verdict-only close-out.

Convergence asks whether fixes are settling or each fix creates the next
problem. A fresh finding in code no fix touched is a missed issue, not a
signal: it's fixed like any other. Only fix-induced findings and regressions
can mark a run as not converging.

**Ledger.** Record the SHA at each cycle's start. For every item verified
CONFIRMED or PARTIALLY RIGHT (own findings and existing feedback alike) add a
line: cycle, id, severity, `file:symbol`, invariant or concern broken, fixing
commit (once known), origin. Existing feedback has no severity; the
coordinator assigns one by the Comment bar. The coordinator assigns origin:
- `regression of <id>` — matched a thread this run fixed.
- `fix-induced from <parent id>` — its lines were last written by the
  parent's fix commit (blame against commits since cycle 1's start SHA; the
  `simplify` commit doesn't count).
- `fresh` — anything else.

Unverified, rejected, deferred, and dropped findings never enter the ledger;
a claim the skeptic drops becomes a no-fix record instead.

**Decide** after each cycle's verification, checking the rows in this order
and taking the first that matches. Only `issue:` findings count, except
regressions, which count at any severity. Nits and suggestions never block
convergence and are fixed along the way.

1. **Not converging** — any of:
   - **No net progress** (never in cycle 1): the previous cycle's fixes
     induced at least one verified `issue:` finding, and at least as many as
     the `issue:` findings they fixed.
   - **Fix chain**: a fix-induced finding whose parent was fix-induced.
   - **Regression**: a finding tagged `regression of <id>` (any severity), or
     an earlier fix's regression test going red.
   → Halt: see "On non-convergence".
2. **Converged** — zero verified `issue:` findings this cycle (fresh,
   fix-induced, or from existing feedback; deferred ones don't count), and
   row 1 didn't match. Possible
   as early as cycle 1. → **Stop the run**: fix this cycle's nits and
   suggestions in the same round, then end. Start no further cycle and no
   re-review, even with cycles left; those last fixes are unreviewed, so
   say so in the report. The verification gate must still pass.
3. **Continue** — verified `issue:` findings exist and row 1 didn't match
   (fresh findings and a single fix-induced bug are normal corrections):
   fix, then run the next cycle.
4. **At the cap** (cycle N) rows 1–3 decide the same way, but row 3 fixes and
   then ends with no next cycle. The run is "capped": those fixes are
   unreviewed, and the report says so.

**On non-convergence, stop patching and bring the user into the loop.**
Another local fix would be a bolt-on. Take the verdict-only close-out for
the whole cycle (nits and suggestions stay unfixed too) so the verified
threads stay open as evidence, and start no further cycle. Then:

1. Design-review the hotspot: read its ledger lineage, name the shared root
   cause (unclear state ownership, a missing abstraction, an unmodeled
   lifecycle), and propose one or two design-level fixes with tradeoffs and
   blast radius.
2. Ask the user, naming the signal that fired and
   presenting it as "a real design issue, not patch material". Options: apply
   a proposed design fix, rethink it differently (their direction), or
   stop here with threads left open.
3. Wait. Apply no design change and open no ticket without their answer.
4. If they pick a design change, have the fix stage apply it as one root-cause
   change and run the gate; the coordinator commits and pushes, then resumes the
   loop. Their decision restarts the cycle cap and the ledger lineage for that
   hotspot, even if the run had reached the cap, and the open threads reach
   the next cycle as unresolved digest items. If they stop, go to the final
   report.

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
