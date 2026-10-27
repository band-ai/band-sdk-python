---
name: band-release-docs
description: Release notes for band-sdk releases. CI's release-announce workflow uses the changelog task to turn a release's PRs into structured changelog items.
disable-model-invocation: true
---

# Release Docs

Release notes for `band-sdk-v*` releases of this repository. Claude only
judges and writes here: tested scripts in `scripts/` collect the input,
validate every item against the release, render the Fern entry and the
announcements, and perform every side effect.

## Tasks

| Task | Where it runs | Guide |
|---|---|---|
| `changelog` | CI, `.github/workflows/release-announce.yml` | [changelog.md](changelog.md) |

The entry format has one source, [the format reference](references/fern-changelog-format.md),
enforced by `scripts/validateentry.py`; guides link to it rather than restate it.

## Scripts

Each script runs standalone with `uv run --script scripts/<name>.py` and
carries its dependencies inline. Data shapes live in `scripts/releasedata.py`.

| Script | Does |
|---|---|
| `collectprs.py` | Gates the release and collects its PRs into `release-prs.json` |
| `reportschema.py` | Prints the report schema for `claude --json-schema` |
| `extractreport.py` | Reads the report from claude-code-action's execution file |
| `builditems.py` | Traces every item to the release, rejects unsafe text, writes `release-notes.json` |
| `renderentry.py` | Renders and validates the Fern entry, standalone or merged into a date file |
| `announcement.py` | Formats the Slack or Discord post |
| `postslack.py`, `postdiscord.py` | Post to the webhooks; Discord also deletes by message id |
