# Distribution

1. **Digest, once per cycle.** First confirm the checkout still equals the
   PR's `headRefOid` (fix rounds push). Read the diff and fetch feedback following
   [the shared intake rules](../band-here-is-a-review/pr-feedback.md). First pick
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
