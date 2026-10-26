# Fern task (local): `/band-release-docs <tag> [--bundle <dir>] [--rehearse]`

A maintainer runs this in Claude Code from this repository's checkout after
`release-announce.yml` has attached the release's bundle. It files the Fern
changelog PR from the bundle, then opens one Fern docs PR updating the Python
SDK pages the release made stale. Every push, PR, and close waits for the
maintainer to review the diff and approve.

`S` below is `.claude/skills/band-release-docs/scripts`; run each script with
`uv run --script "$S/<name>.py"`.

## Before starting

- `gh auth status` must show push access to `thenvoi/fern`. This repository's
  CI holds no Fern credential; the PRs are the maintainer's.
- Ask for a local `thenvoi/fern` clone, or `gh repo clone thenvoi/fern` into a
  temporary directory.
- `--bundle <dir>` reads the bundle from a directory (for example
  `gh run download <run-id> --name bundle --dir <dir>`) instead of the release.
- `--rehearse` uses `-rehearsal` branch suffixes and never pushes, opens,
  closes, or comments on anything. It still runs the existing-PR check on the
  real branch names and reports what it finds, without stopping.

## Branches

Changelog: `changelog/python-sdk-<tag>`. Docs: `docsrange.py`'s
`DOCS_BRANCH_PREFIX` followed by the tag (`docs/python-sdk-<tag>`).

For each branch, first run
`gh pr list --repo thenvoi/fern --head <branch> --state all --json number,state,url`.
An open or merged PR stops that part and links it. Otherwise create the
worktree with `git -C <fern> worktree add -B <branch> <tmp> origin/dev`
(resetting a stale local branch). Push with
`git -C <worktree> push --force-with-lease origin <branch>`: the branch tracks
`origin/dev`, so a bare push would target `dev`, and Fern deletes merged
branches but keeps those of closed, unmerged PRs.

## Changelog

1. Fetch the bundle: `gh release download <tag> --repo band-ai/band-sdk-python --dir <tmp>`.
   No bundle means the release had no user-facing changes or its run failed:
   stop, say so, and link
   `https://github.com/band-ai/band-sdk-python/actions/workflows/release-announce.yml`.
2. `git -C <fern> fetch origin dev`, then the branch check and worktree above.
3. Holdbacks: compare each item in `release-notes.json` (numbered from 0)
   against `<fern>/.changelog/held-items.yml` entries whose surfaces include
   `sdks`, and against features marked hidden or Enterprise-gated in
   `<fern>/docs-structure-summary.md`. Show every match and ask the maintainer
   whether to hold it back; the ledger requires that confirmation.
4. Merge the entry:
   `"$S/renderentry.py" release-notes.json --merge-into <worktree>/fern/changelog/sdks --drop <held index>... --pr-body pr-body.md`.
   It validates the entry against the [format reference](references/fern-changelog-format.md)
   and checks that every existing line of the date file survives in order.
5. Show the diff. After approval: commit with no AI attribution (Fern's
   `.claude/claude.md`), push as above, and
   `gh pr create --repo thenvoi/fern --base dev --head <branch> --title "docs(changelog): Python SDK <tag>" --body-file pr-body.md`.

## Docs

6. Range: list the docs PRs into two files with
   `gh pr list --repo thenvoi/fern --state merged --search 'head:docs/python-sdk-band-sdk-v' --json number,headRefName`
   and the same with `--state open`, then run
   `"$S/docsrange.py" <tag> merged.json open.json`. It prints `baseline` and
   `supersede`. Build the review input with
   `"$S/collectprs.py" <tag> --since <baseline> > review-prs.json`.
7. Check out the released source: `git worktree add --detach <tmp>/sdk <tag>`.
   Then the branch check and Fern worktree on the docs branch.
8. Nominate at most `MAX_REVIEWED_PAGES = 10` pages anywhere under
   `fern/docs/pages/integrations/sdks/` (including `tutorials/`). Start from
   the files, titles, and bodies in `review-prs.json` and search the pages for
   the names those PRs changed. Record each nomination as page, reason, and
   source PRs. Review each page against the released source in `<tmp>/sdk`,
   including every snippet's imports and names.
   - Edit only what a PR in the range made wrong. Staleness that predates the
     range is listed in the PR body, not edited.
   - Follow the writing rules in Fern's `.claude/skills/write-docs/STYLE.md`
     and `.claude/fern-docs-elements-guide.md`; their ledger and scoping steps
     are for Fern's own docs work and do not apply here.
   - Leave untouched any feature held in `.changelog/held-items.yml` or marked
     hidden or Enterprise-gated in `docs-structure-summary.md` (step 3's
     sources).
   - Describe changes as of `<tag>`. Write no new pages.
9. If nothing is stale, open no docs PR and say so. Otherwise write the PR
   body: Summary; Pages reviewed (reason and source PRs for each); Claims (each
   changed fact with a link to
   `https://github.com/band-ai/band-sdk-python/blob/<tag>/<path>#L<line>`);
   Also stale (drift predating the range, with the same links); Pages missing
   (framework adapters exported from `src/band/adapters/` at the tag that no
   page under `tutorials/` documents, where one page may cover several); and
   `Supersedes #N` for each PR in `supersede`.
10. Show the diff. After approval: commit, push as above,
    `gh pr create --repo thenvoi/fern --base dev --head <branch> --title "docs: Python SDK <tag>" --body-file <body>`,
    and, on confirmation, close each superseded PR with the comment
    `Superseded by #M`.

A person approves and merges both PRs in Fern; promotion from `dev` stays
manual.
