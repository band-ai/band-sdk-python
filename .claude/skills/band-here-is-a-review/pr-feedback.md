# Fetching PR feedback

Shared by review, remediation, and readiness workflows. Read only the
material the caller needs; fetching feedback never authorizes publication.

- Pull the review threads with `gh api graphql` on the PR's `reviewThreads`: each
  thread's `id`, `isResolved`, `isOutdated`, `path`, `line`, and its comments
  (`author { login }`, `body`). Walk `pageInfo` until `hasNextPage` is false. The
  thread `id` is all Phase 6 needs to reply and resolve.
- Also pull review bodies (`gh api --paginate repos/{o}/{r}/pulls/{n}/reviews`) and
  top-level comments (`gh api --paginate repos/{o}/{r}/issues/{n}/comments`) for
  findings that sit outside a thread. These have no resolved state: retain each
  comment's ID and URL, and skip a finding only when a later reply explicitly
  records its published fix, rejection, deferral, authorized drop, or answer.
  A confirmation with a proposed but unpublished fix remains open; an unrelated
  author comment is not an acknowledgement. Before skipping a claimed fix,
  verify that it still addresses the finding at the current head.
  Skip empty review bodies (the wrapper around a batch of inline comments).
- Handle only unresolved threads. An outdated thread still counts: verify it
  against the current code like any other. Skip `band-code-review`'s no-fix
  records (`.claude/skills/band-code-review/no-fix-records.md`): they are
  dropped claims, not feedback.
- Read the whole thread, not just its first comment — later replies may narrow,
  withdraw, or already answer the claim.

For readiness and review-history analysis, include resolved threads as history,
but do not reopen or answer them merely because they were fetched. Paginate
thread lists and nested comments; retain comment IDs and URLs with verdicts.
