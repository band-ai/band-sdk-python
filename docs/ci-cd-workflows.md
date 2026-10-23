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
  `test-parlant` suites on Ubuntu with Python 3.12. `test-strands-nooa`, `packaging`
  and `wheel-extras` stay out of the queue, and
  `python-core-coverage.yml` does not run in the queue. A break that shows only on a lane the
  queue skips reaches `main` and surfaces in the next PR's CI.
- **Normal PRs run all four dependency environments on Linux with Python 3.11–3.13,
  and on Windows with Python 3.13 only.** The full Linux/Windows grid runs on the
  `release-please--branches--main` release PR, every Monday at 03:17 UTC, and through
  `gh workflow run ci.yml --ref <branch-or-tag>`. Scheduled runs use the latest `main`
  commit. Manual runs validate the selected ref without publishing anything.
  When adding a supported Python version, update the matrix exclusions so normal
  PRs retain only the newest validated Windows interpreter; the matrix guard
  checks this against the version grid.

## Unit pytest parallelism

- **Unit jobs run pytest-xdist worksteal inside the existing OS × Python
  cells** (`-n auto --dist worksteal --maxprocesses=4`). Every extra
  (`test`, `test-crewai`, `test-parlant`, `test-strands-nooa`) uses those
  flags on every OS in the matrix, so each runner saturates the 4 vCPUs it
  already has. They do not add a GitHub Actions shard axis. Markdown-docs
  and E2E collect-only stay serial. Do not put `-n` in pytest `addopts` or
  `PYTEST_ADDOPTS`: local live runs (`E2E_TESTS_ENABLED` and the other suite
  gates) and `--pdb` must stay single-process. Use `pytest -n0` to debug.
- **Durations rebalance** is `.github/workflows/pytest-durations.yml`:
  Mondays at 04:17 UTC (after weekly CI) and on `workflow_dispatch`. It
  times the unit suite with the same in-job xdist flags on one Ubuntu
  runner, writes `.test_durations`, and opens a PR when the file drifts. A
  quiet week opens nothing. CI reads that file so historically slow tests
  start first and the four workers stay busy. This is not extra PR jobs.

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
