# CI/CD Workflows Guide

The workflows are in `.github/workflows/`. This page holds what lives server-side or that
the workflow files cannot say for themselves.

## Branch protection

- **Rules live in GitHub Rulesets, not in the repo.** Read them with
  `gh api repos/band-ai/band-sdk-python/rulesets` instead of a hand-kept table.
- **`ci-status` is the rollup CI check, meant to be required alongside `Validate PR Title`.**
  It is the last job in `ci.yml`: a table in its script names every job it `needs` with the
  results that job may end in, and any other result turns it red, a draft pull request
  included, since a draft runs no CI. Requiring it instead of each job means no matrix cell is
  named in the ruleset and `ci.yml` may run a different job set per event. A new `ci.yml` job
  joins the contract by being listed in `ci-status.needs` and in that table;
  `tests/framework_conformance/test_ci_status_coverage.py` fails until it is. The same gate
  shape runs in `band-sdk-typescript`.
- **`main` uses a merge queue**, so every workflow whose check the queue waits for needs a
  `merge_group:` trigger (`ci.yml` and `pr-title.yml`; `release-gate.yml` reports there too).
  Without it the queue waits forever for a check that never starts. `main` fast-forwards to
  the exact commit the queue validated, so CI does not run again on a push to `main`.
- **A new push to a pull request cancels its running CI.** Merge-queue runs are never
  cancelled or queued behind one another: each one is the verdict for the commit that
  lands, in a concurrency group of its own.
- **The merge queue runs a slim `ci.yml`:** `lint` plus the `test`, `test-crewai` and
  `test-parlant` suites on Ubuntu with Python 3.12. The full OS × Python grid,
  `test-strands-nooa`, `packaging` and `wheel-extras` run on the PR only, and
  `python-core-coverage.yml` does not run in the queue. A break that shows only on a lane the
  queue skips reaches `main` and surfaces in the next PR's CI.

## Private Core coverage

- `python-core-coverage.yml` checks out the private Core repository with
  `CORE_SDK_READ_KEY`. Fork pull requests and Dependabot cannot receive that secret,
  so this job skips those contexts rather than attempting an unauthenticated checkout.
- The ordinary CI test matrix still runs for fork pull requests. Core coverage runs
  for trusted pull requests, merge groups, scheduled runs, and manual dispatches.
- To validate a fork revision before merge, a maintainer can publish its reviewed
  commit to a temporary upstream branch and run
  `gh workflow run python-core-coverage.yml --repo band-ai/band-sdk-python --ref <branch>`.
  Check that the run's head SHA matches the reviewed commit, wait for coverage and
  reporting to finish, then remove only that temporary branch. The manual workflow
  also posts its normal coverage digest; no secrets should be exposed to fork code.

## PyPI publish

- **Trusted publishing binds to the top-level filename `band-publish.yml`** (with environment
  `release`), which is why it is neither a reusable workflow nor a job in `release.yml`.
- **The release event runs the workflow file at the tagged commit.** That is why a tag ruleset
  restricts creating, moving and deleting `band-sdk-v*` tags to the release App and repo admins:
  the tag namespace the deployment policy trusts must not be claimable by a write collaborator.
- **The `release` environment's deployment policy** is what keeps a modified copy of the
  workflow on a side branch from reaching the OIDC credential. Read it with
  `gh api repos/band-ai/band-sdk-python/environments/release/deployment-branch-policies`.
- **Recovery for a failed publish:** run `band-publish.yml` by `workflow_dispatch` with the
  existing tag. `skip-existing` makes re-runs idempotent; no retag or re-release is needed.

## Validating workflow changes before merge

- actionlint runs through pre-commit and the CI `lint` job. The one ignored false positive is
  documented in `.pre-commit-config.yaml`.
- **Probe-branch dispatch is the server-side ground truth.** GitHub's startup validation
  evaluates env templates even under a false `if:` and type-checks reusable-workflow `with:`
  values (a dispatch `number` input arrives as a string; numbers need `fromJSON`). Push a
  branch and run `gh workflow run <file> --ref <branch>`. It is safe because the `release`
  environment policy rejects refs it does not allow before a job can reach publishing
  credentials, so the probe fails at that gate, which also checks the guardrail still works.
- **Rehearsal publish:** `kit-publish-manual` with version `0.0.0-rcN` and `move-floating: false`
  publishes a disposable artifact (runbook: `docker/band_python_kit/RELEASING.md`).
