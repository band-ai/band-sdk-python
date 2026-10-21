---
name: band-ci-triage
description: Investigate failing GitHub Actions checks for a PR or commit in the Band Python SDK. Use for "why is CI red", "triage these checks", or "fix CI on PR 123". Attribute failures to the tested revision and dependency lane, reproduce the failing boundary, and fix only when requested. Use band-pr-triage for merge-readiness checks and band-investigate-test-error for a failing test already isolated locally.
---

# CI Triage

Read [the shared runtime guidance](../workflow-runtime.md) and
`docs/ci-cd-workflows.md`. A diagnosis request is read-only; an explicit fix
request authorizes in-scope implementation without a second plan-approval
pause. Reuse existing authorization for pushes, reruns, and publication.

## Establish the tested revision

1. Resolve the repository and PR or commit from the user's input. Record the
   PR's current `headRefOid`, base, and check URLs; inspect the worktree before
   editing. Do not replace local work or switch a worker's shared checkout.
2. Inspect check status and the failing run's `headSha`, `event`, `attempt`,
   job, OS, and Python version. A stale run is evidence about its own revision,
   not a verdict on the current PR. Merge-queue runs test a candidate commit;
   compare it with the PR head and base instead of treating different SHAs as
   automatically irrelevant.
3. Get the first actionable failure and enough log context to explain it.
   Separate primary errors from downstream rollup failures such as
   `ci-status`. Pending, cancelled, skipped, and unavailable checks are not
   passing checks. Do not wait for unrelated jobs unless requested.

Use the installed CLI's help as the command contract. Typical reads:

```bash
gh pr view <pr> -R <owner/repo> --json headRefOid,baseRefName,statusCheckRollup
gh pr checks <pr> -R <owner/repo> --json name,state,bucket,link,workflow
gh run view <run-id> -R <owner/repo> --json headSha,event,attempt,status,conclusion,jobs,url
gh run view <run-id> -R <owner/repo> --log-failed
gh run view -R <owner/repo> --job <job-id> --log
```

If logs are inaccessible or the check belongs to another service, report the
details URL and missing evidence. Use that service's available tools only
within the requested scope; do not guess its API or call it a code failure.
Redact credentials and sensitive message contents from excerpts.

## Attribute and reproduce

- Read the failing workflow and its setup scripts at the tested revision.
  Derive the extra and test command from that job, not the adapter's name.
  The dependency environments and platform restrictions live in
  `pyproject.toml`, `AGENTS.md`, and the baseline registry.
- Separate product/test failures, dependency or packaging failures, workflow
  wiring, missing prerequisites, and external infrastructure. Verify each
  attribution; a network or auth error does not prove the whole job was
  unrelated to the change.
- Reproduce the smallest relevant boundary in the matching environment.
  Use the repository's isolated lane commands for concurrent runs so one
  investigation does not rewrite another's `.venv`. For non-obvious test
  failures, use [band-investigate-test-error](../band-investigate-test-error/SKILL.md).
- For workflow startup, merge-queue, or release failures, consult the canonical
  CI/CD guide. Do not disable required jobs, weaken assertions, increase
  retries, or publish artifacts to manufacture a green result.

## Fix and report

When fixes are requested, repair the owning cause and validate according to
`AGENTS.md` "Pre-Commit Checklist". Add a behavioral regression for a real
code defect; a proven infrastructure incident needs evidence, not a source
workaround. Keep unknown causes unknown.

After an authorized push, re-read the PR head and its checks. Never reuse a
green result from the previous head. Report each failure's revision/job,
cause and evidence, fix commit or next experiment, local checks, and any
pending or unrun checks. If the user says not to wait for CI, report the
current state and finish without polling. Do not merge a PR through this skill.

Command references: [checks](https://cli.github.com/manual/gh_pr_checks),
[run details and logs](https://cli.github.com/manual/gh_run_view).
