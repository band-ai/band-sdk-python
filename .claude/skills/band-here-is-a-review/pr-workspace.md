# Preparing a PR workspace

Shared by `band-here-is-a-review` and `band-code-review`. Run it once, before reading any
code, whenever the work targets a GitHub PR.

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
4. **Check push access** (only when fixes will be pushed). From `gh pr view --json
   isCrossRepository,maintainerCanModify,headRepositoryOwner`: pushing works for a
   same-repo PR, a fork the user owns (`headRepositoryOwner.login` equals
   `gh api user --jq .login`), or a fork with `maintainerCanModify` true. Otherwise
   nothing can be pushed — say so and fall back to replying only.
