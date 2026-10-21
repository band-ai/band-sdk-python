---
name: band-linear-create-branch
description: Create and check out Git branches for Linear work with the installed `git lb` command. Use whenever the user asks to create, start, or switch to a branch for Linear work, even without an issue ID; infer the relevant issue from the available context and ask the user to validate it before creating the branch.
---

# Linear Create Branch

Use `git lb` as the single source of truth for the branch name. It retrieves the
Linear issue, maps its labels to a conventional prefix, creates the branch, and
checks it out. Do not construct a branch name manually.

## Workflow

1. Use the available context to deduce the relevant Linear issue. Prefer an
   explicit issue ID when the user provides one.
2. Before changing Git state, state the inferred issue and ask the user to
   validate it. If the context supports multiple plausible issues, offer the
   relevant options instead. If it supports none, ask for the issue ID.
3. After the user validates an issue, run the command from the target Git
   repository:

   ```bash
   git lb TEAM-123
   ```

4. For a preview-only request, use:

   ```bash
   git lb --dry-run TEAM-123
   ```

5. Report the resulting branch name or the command error concisely.

## Prerequisites and failures

- Confirm `git lb` is available before invoking it. If it is missing, explain
  that the Linear Branch Tools Git command must be installed and ask the
  developer for the branch name; never generate one yourself.
- `git lb` requires `LINEAR_API_KEY` to fetch the issue. If it is unset or the
  issue cannot be found, relay the error and stop.
- Leave any uncommitted work untouched. Let Git reject a checkout that would
  overwrite local changes, then report that conflict to the user.
