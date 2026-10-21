# Posting mechanics

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
