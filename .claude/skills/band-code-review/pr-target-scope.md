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
4. Pass the scope statement to every subagent.

**In scope** = the diff introduced it, changed it, or made it newly reachable
or newly wrong. Proximity isn't scope: a pre-existing issue near a touched
line that the diff didn't create or affect is out of scope for every group.
*Example:* the diff changes `OrderService.submit`'s validation; a defect in
`submit` is in scope, the same missing check in untouched `cancel` is not.
