# Fern task (local): `/band-release-docs <tag> [--bundle <dir>] [--rehearse]`

`release-announce.yml` files and merges both Fern PRs after every release.
This is the manual recovery path its alerts point to: a maintainer runs it in
Claude Code from this repository's checkout after the workflow has attached
the release's bundle. It files the Fern changelog PR from the bundle, then
opens one Fern docs PR updating the Python SDK pages the release made stale.
Every push, PR, and close waits for the maintainer to review the diff and
approve.

`S` below is `.claude/skills/band-release-docs/scripts`, `<bundle>` is the
directory holding the bundle, and `<tmp>` is a scratch directory outside this
checkout.

## Before starting

- `gh auth status` must show push access to `thenvoi/fern`; the PRs are the
  maintainer's.
- Ask for a local `thenvoi/fern` clone, or `gh repo clone thenvoi/fern` into a
  temporary directory.
- `--bundle <dir>` reads the bundle from a directory (for example
  `gh run download <run-id> --name bundle --dir <dir>`) instead of the release.
- `--rehearse` uses the rehearsal branches below and never pushes, opens,
  closes, or comments on anything. It still runs the existing-PR check on the
  real branch names and reports what it finds, without stopping.

## Branches

Changelog: `CHANGELOG_BRANCH_PREFIX` + `<tag>`. Docs: `DOCS_BRANCH_PREFIX` +
`<tag>`. `--rehearse` appends `REHEARSAL_SUFFIX`. All three are in
`scripts/releasedata.py`.

For each branch, first run
`gh pr list --repo thenvoi/fern --head <branch> --state all --json number,state,url`.
An open or merged PR stops that part and links it. Then run
`git -C <fern> worktree prune` and remove with `git -C <fern> worktree remove`
any worktree `git -C <fern> worktree list` still shows on the branch. If git
refuses because it holds changes, show them and ask before `--force`. Before
resetting the branch, `git -C <fern> log --oneline <branch> --not --remotes`
(when the local branch exists) lists commits on no remote: show them and ask
first. Either may be the maintainer's hand edits. Then create the worktree with
`git -C <fern> worktree add -B <branch> <worktree> origin/dev` (resetting a stale
local branch). Push with
`git -C <worktree> push --force-with-lease origin <branch>`: the branch tracks
`origin/dev`, so a bare push would target `dev`. The lease compares against the
remote branches as last fetched, which is why step 2 fetches with `--prune`: a
closed PR may keep its branch or have it deleted.

## Changelog

1. Fetch the bundle: `gh release download <tag> --repo band-ai/band-sdk-python --dir <bundle>`
   (with `--bundle <dir>`, `<bundle>` is that directory).
   No bundle means the release had no user-facing changes or its run failed:
   stop, say so, and link
   `https://github.com/band-ai/band-sdk-python/actions/workflows/release-announce.yml`.
2. `git -C <fern> fetch --prune origin`, then the branch check and worktree
   above. Then run
   `uv run --script "$S/fernstate.py" <tag> --out <tmp>/state --date <release_date>`
   (`release_date` is in `<bundle>/release-notes.json`; add `--rehearsal`
   with `--rehearse`). When it fails because another open PR changes
   `fern/changelog/sdks/<release_date>.md`, stop and link that PR: both PRs
   would write that date file, so it merges first and this command runs again.
3. Holdbacks: apply [holdbacks.md](holdbacks.md) to `<bundle>/release-notes.json`
   with `<fern>`'s ledger and docs summary. The bundle the workflow attached
   already leaves out what it held; show every further match and ask the
   maintainer whether to hold it back.
4. Merge the entry:
   `uv run --script "$S/renderentry.py" <bundle>/release-notes.json --merge-into <worktree>/fern/changelog/sdks --drop <held index>... --pr-body <tmp>/pr-body.md`.
   It validates the entry against the [format reference](references/fern-changelog-format.md),
   puts this release's block above the date file's earlier ones (rule 0), and
   checks that every existing line survives in order. It refuses, writing
   nothing, when the date file:
   - already holds this release's subtitle: it is filed in Fern `dev`, perhaps
     under another branch name. Stop and link the date file.
   - holds a newer release of this SDK: blocks go newest first, and this
     command only files a block on top. Stop and show both releases; the
     maintainer files this one by hand.
   - has entries without `## <SDK> <version>` subtitles (filed before Fern's
     rule 0): stop and show the file. Add those subtitles only with the
     maintainer's approval, in a separate commit, then run this step again.
5. Show the diff. After approval: commit with no AI attribution (Fern's
   `.claude/claude.md`), push as above, and
   `gh pr create --repo thenvoi/fern --base dev --head <branch> --title "docs(changelog): Python SDK <tag>" --body-file <tmp>/pr-body.md`.

## Docs

6. Range: `git fetch --tags origin`, then list the docs PRs with
   `uv run --script "$S/fernstate.py" <tag> --out <tmp>/docs-state` and run
   `uv run --script "$S/docsrange.py" <tag> <tmp>/docs-state/docs-merged.json <tmp>/docs-state/docs-open.json`. It prints `baseline` and
   `supersede`, or refuses when this release's or a newer one's docs are
   already merged (a review as of `<tag>` would repeat or roll them back), or
   when an open docs PR for a newer release already covers `<tag>`. Stop and
   say so, linking that PR: it must merge or be closed before `<tag>` gets a
   review of its own. Build the
   review input with
   `uv run --script "$S/collectprs.py" <tag> --since <baseline> > <tmp>/review-all.json`,
   then drop the held PRs with
   `uv run --script "$S/withhold.py" <tmp>/review-all.json <bundle>/release-notes.json --ledger <fern>/.changelog/held-items.yml --held <index>... > <tmp>/review-prs.json`,
   passing the indexes step 3 held.
7. Check out the released source: `git worktree add --detach <tmp>/sdk <tag>`
   (first `git worktree remove --force <tmp>/sdk` if an earlier run left it; it
   is a read-only checkout, removed again after step 9 or 10).
   Then the branch check and Fern worktree on the docs branch.
8. Review the pages by [docs.md](docs.md), with the Fern worktree as `<fern>`,
   `<tmp>/sdk` as `<sdk>`, and `<tmp>/review-prs.json` as the review input.
9. If nothing is stale, open no docs PR, say so, and remove `<tmp>/sdk`.
   Otherwise write the PR body to `<tmp>/docs-pr-body.md` with the sections
   `scripts/docsreport.py` renders from docs.md's report, linking each claim to
   `https://github.com/band-ai/band-sdk-python/blob/<tag>/<path>#L<line>`,
   and `Supersedes #N` for each PR in `supersede`.
10. Check the edits with the workflow's rules: `git -C <fern> worktree add --detach <tmp>/base origin/dev`,
    copy both trees without `.git`
    (`rsync -a --exclude .git <tmp>/base/ <tmp>/pristine/` and
    `rsync -a --exclude .git <worktree>/ <tmp>/edited/`), run
    `uv run --script "$S/checkpatch.py" <tmp>/pristine <tmp>/edited --out <tmp>/patch.json`,
    then `git -C <fern> worktree remove --force <tmp>/base`. A path violation
    stops the docs PR; list each content violation under `## Do not merge` in
    the PR body and show it to the maintainer.
    Show the diff. After approval: commit, push as above,
    `gh pr create --repo thenvoi/fern --base dev --head <branch> --title "docs: Python SDK <tag>" --body-file <tmp>/docs-pr-body.md`,
    and, on confirmation, close each superseded PR with the comment
    `Superseded by #M`. Finally `git worktree remove --force <tmp>/sdk`.

A person approves and merges both PRs in Fern; promotion from `dev` stays
manual.
