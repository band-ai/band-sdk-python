# CI/CD Workflows Guide

The workflows are in `.github/workflows/`. This page holds what lives server-side or that
the workflow files cannot say for themselves.

## Branch protection

- **Rules live in GitHub Rulesets, not in the repo.** Read them with
  `gh api repos/band-ai/band-sdk-python/rulesets` instead of a hand-kept table.
- **A matrix job reports one check context per cell**, named `test (<os>, <python-version>)`.
  Require the exact string: a name that matches no real context silently never gates.
- **`main` uses a merge queue**, so every required workflow needs a `merge_group:` trigger
  (`ci.yml`, `pr-title.yml`, `release-gate.yml` and `python-core-coverage.yml` have one).
  Without it the queue waits forever for a check that never starts.

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
