# Preparing a PR workspace

Shared by `band-here-is-a-review` and `band-code-review`. Run it once, before reading any
code, whenever the work targets a GitHub PR.

Follow [workflow-runtime.md](../workflow-runtime.md) for tools and existing
authorization. Workers inspect the prepared checkout; only the coordinator
changes branches or prepares another checkout.

1. **Identify the PR.** A PR URL gives `owner/repo` and the number; a bare number or
   branch name means the current repo. Pass `-R owner/repo` to every `gh pr` command
   and use the same `owner/repo` in every `gh api` path.
2. **Use a clone of that repo.** If the current repo (`gh repo view --json
   nameWithOwner`) isn't the PR's repo, ask the user for the path to their clone. With
   no clone, a review-only run may use a fresh clone in the scratchpad; fixing needs
   the user's clone.
3. **Check out the PR head.** If the current branch isn't the PR's head branch, run
   `gh pr checkout <n> -R owner/repo`, which also handles fork PRs. Ask first if the
   working tree has uncommitted changes. Then `git rev-parse HEAD` must equal the PR's
   `headRefOid`. If it doesn't (unpushed local commits, or the remote moved ahead),
   stop and tell the user how they differ. Line numbers and pushes are only safe in
   sync.
4. **Check push access** (only when fixes will be pushed). Inspect the PR's
   `isCrossRepository`, `maintainerCanModify`, and head repository, together with
   the authenticated user's permissions. Direct write access to the head repository
   covers same-repo and owned-fork PRs. The separate maintainer route requires
   push access to the base repository and the creator's permission to edit the
   user-owned fork (`maintainerCanModify`); it does not require direct fork write
   access. Check branch restrictions too; see
   [GitHub's fork editing requirements](https://docs.github.com/en/pull-requests/how-tos/commit-changes/committing-changes-to-a-pull-request-branch-created-from-a-fork).
   If neither route permits pushing, enter verdict-only mode before any edits.
   Skip implementation and commits, propose fixes in replies, and leave confirmed
   or partially-right defects open until a fix is published.
