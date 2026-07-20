# CI/CD Workflows Guide

This document explains the GitHub Actions workflows and branch protection used in
this repository.

## Overview

The repository uses **GitHub Flow** on a single long-lived trunk:

```
feature branch ──(squash PR)──▶ main ──(merge release PR)──▶ release (PyPI + GHCR)
```

- `main` — the single trunk. All feature work is squash-merged here, and it is
  also the release branch.
- There is no `dev`/`develop` branch and no promotion hop. Release Please keeps a
  standing **release PR** up to date on `main`; merging that release PR is the
  deliberate "cut a release" action.

Because feature work and release commits live on the same branch, there is no
back-merge to reconcile after a release.

## Branch Protection (GitHub Rulesets)

Protection is enforced with GitHub Rulesets, not classic branch protection.

| Branch | Merge Method | Required Reviews | Stale Dismissed | Thread Resolution | Strict Checks |
|--------|--------------|------------------|-------------------|---------------|---------------|
| `main` | Squash (features) / Merge (release PR) | 1 | Yes | Yes | Yes |

`main` blocks deletion and non-fast-forward (force) pushes.

**Required status checks:**

- `lint`
- `test (3.11)`
- `test (3.12)`
- `packaging`
- `Validate PR Title`

> `main` uses **strict** required status checks: a PR branch must be up to date
> with `main` before it can merge. After each merge, other open PRs go stale and
> must click **Update branch** (which re-runs the required checks) before they can
> merge. This keeps `main` provably green at every commit at the cost of some
> update-branch churn on concurrent PRs.

## PR Workflows

### CI — `ci.yml`

Runs on every PR to `main`.

- `lint` — `pre-commit run --all-files`
- `test` — pytest on Python 3.11 and 3.12 (Linux + Windows)
- `test-crewai` — crewai adapter/converter tests in an isolated extra
- `packaging` — builds the wheel and verifies core and full imports

### PR Title — `pr-title.yml`

Validates the PR title against Conventional Commits (`Validate PR Title`).
Skipped for bot actors (dependabot, release-please).

## Release Workflow

### Release — `release.yml`

Triggered on push to `main` (i.e. on every merge, and again when a release PR
merges).

1. Release Please opens/updates a release PR, or — when a release PR merges —
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

## Typical Development Flow

1. Branch off `main` (e.g. `feat/...-INT-123`).
2. Open a PR to `main` → CI + PR-title checks run.
3. Get 1 approval, **squash** merge to `main`.
4. Release Please updates the standing release PR to reflect the new commit.
5. When ready to release, review and **merge the release PR**. That tags the
   release and triggers `release.yml`, which publishes to PyPI and GHCR.

> **No back-merge:** the release commit Release Please lands (version bump +
> CHANGELOG) goes onto `main` — the same branch everyone works from — so there is
> nothing to reconcile back into a separate integration branch.
