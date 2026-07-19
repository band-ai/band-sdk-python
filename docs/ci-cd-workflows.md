# CI/CD Workflows Guide

This document explains the GitHub Actions workflows and branch protection used in
this repository.

## Overview

The repository uses a branch promotion model with two long-lived branches:

```
feature branches ──(squash)──▶ dev ──(merge)──▶ main (release)
```

- `dev` — integration branch. All feature work is squash-merged here.
- `main` — release branch. Merges to `main` trigger an automated PyPI release
  via release-please.

There is no `staging` branch; promotion is a single hop `dev → main`.

## Branch Protection (GitHub Rulesets)

Protection is enforced with GitHub Rulesets, not classic branch protection.

| Branch | Merge Method | Required Reviews | Stale Dismissed | Thread Resolution | Strict Checks |
|--------|--------------|------------------|-----------------|-------------------|---------------|
| `dev`  | Squash only  | 1                | No              | No                | No            |
| `main` | Merge commit | 1                | Yes             | Yes               | Yes           |

Both branches block deletion and non-fast-forward (force) pushes.

**Required status checks (both branches):**

- `lint`
- `test (3.11)`
- `test (3.12)`
- `packaging`
- `Validate PR Title`

> `main` uses **strict** required status checks: the PR branch must be up to date
> with `main` before it can merge.

## PR Workflows

### CI — `ci.yml`

Runs on every PR to `dev` and `main`.

- `lint` — `pre-commit run --all-files`
- `test` — pytest on Python 3.11 and 3.12
- `test-parlant` — parlant adapter/converter tests in an isolated extra
- `packaging` — builds the wheel and verifies core and full imports

### PR Title — `pr-title.yml`

Validates the PR title against Conventional Commits (`Validate PR Title`).
Skipped for bot actors (dependabot, release-please, the promote workflow's
GitHub App). Promotion PRs are titled `chore: promote dev to main` so they remain
valid Conventional Commits if the check does run.

## Release Workflow

### Release — `release.yml`

Triggered on push to `main` (i.e. after a promotion PR merges).

1. release-please opens/updates a release PR, or — when a release PR merges —
   tags the release and updates the changelog and version.
2. On a created release, publishes `band-sdk` to PyPI (`publish-band`).
3. In parallel, publishes the sandbox kit to GHCR via the reusable
   `kit-publish.yml` pipeline (`publish-kit`): the multi-arch attested image
   `ghcr.io/band-ai/band-python-kit/image` and the OCI kit artifact
   `ghcr.io/band-ai/band-python-kit`, gated by the supply-chain quarantine
   check. A kit failure never blocks the PyPI publish (independent jobs).
4. After a successful kit publish, opens an automated version-bump PR against
   `band-ai/add-band` (`bump-add-band`; merging it stays a human step).
5. `summary` reports every artifact's outcome.

Related: `kit-image-rebuild.yml` (weekly CVE rebuild of the published kit
image), `kit-publish-manual.yml` (serialized manual recovery/rehearsal), and
`docker/band_python_kit/RELEASING.md` (tag policy, quarantine gate, rehearsal
and recovery runbook).

## Promotion Workflow

### Promote Dev to Main — `promote-dev-to-main.yml`

A manual (`workflow_dispatch`) workflow that opens the promotion PR for you.

**How to use:**

1. Go to **Actions → Promote Dev to Main**.
2. Click **Run workflow**.
3. Optionally enable **Dry run** to preview the PR description without creating a PR.
4. Review and merge the created PR (normal review + checks still apply).

**What it does:**

1. Mints a GitHub App token (via the local `./.github/actions/GithubToken` action).
2. Checks out `dev` and fetches `main`.
3. Counts commits ahead (`origin/main..dev`). If zero, it reports "already up to
   date" and creates nothing.
4. Builds a PR description from git history — commit list, `git diff --stat`,
   referenced PR numbers, and a metadata footer. (No external AI service is used.)
5. Creates a PR from `dev` → `main` titled `chore: promote dev to main`, assigned
   to whoever triggered the run — unless **Dry run** is enabled, in which case the
   description is printed to the run summary and no PR is created.

The workflow never merges; a human reviews and merges, so all rulesets apply.

## Typical Development Flow

1. Branch off `dev` (e.g. `feat/...-INT-123`).
2. Open a PR to `dev` → CI + PR-title checks run.
3. Get 1 approval, **squash** merge to `dev`.
4. When ready to release, run **Promote Dev to Main** and merge the resulting PR.
5. The merge to `main` triggers `release.yml`, which publishes to PyPI.

> **Note on strict checks:** because `main` uses strict required status checks, a
> release commit that release-please lands on `main` will leave `dev` behind. The
> next `dev → main` promotion PR cannot merge until `dev` contains that commit, so
> `main` must be brought back into `dev` before promoting again.
