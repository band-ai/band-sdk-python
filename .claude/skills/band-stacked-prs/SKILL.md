---
name: band-stacked-prs
description: Use GitHub's native stacked pull requests (public preview, `gh stack` CLI), checking the live GitHub docs first because the feature is new and still changing. Use during any git or PR work in a GitHub repo where a stack may apply, even if the user never says "stack" - a large diff or branch about to become one PR, a new branch cut from an unmerged feature branch or open PR, a PR whose base is not the default branch, work that depends on an unmerged PR, or rebasing, retargeting, reordering, or merging a chain after the bottom PR merged or review feedback lands low in it. Also use when the user mentions stacked PRs, "gh stack", PR chains, or dependent PRs, or hits a `gh stack` error. Skip for a single independent PR, cross-fork PRs, non-GitHub remotes, or other stacking tools such as Graphite, ghstack, git-town, or spr.
---

# Stacked PRs

Read [workflow-runtime.md](../workflow-runtime.md) for available tools and
authorization. Use the current runtime's web access for the docs below.

GitHub's stacked PRs are in public preview, so commands, limits, and API behavior
can change. The docs below are the single source of truth. This skill decides
when to stack and how to work safely; it deliberately does not restate command
syntax, which would go stale.

## Docs

Start from the landing page; it lists the current sub-pages if any moved.

| Need | Page |
|---|---|
| Landing and sub-page index | https://docs.github.com/en/pull-requests/how-tos/stacked-pull-requests |
| Concepts, merge and rebase behavior, protections, limits, preview status | https://docs.github.com/en/pull-requests/get-started/about-stacked-prs |
| Install, prerequisites, first-stack walkthrough | https://docs.github.com/en/pull-requests/get-started/stacked-prs-quickstart |
| Commands, flags, environment variables, exit codes | https://docs.github.com/en/pull-requests/reference/stacked-prs-cli-commands |
| Org rollout, CI behavior, API and merge-endpoint caveats | https://docs.github.com/en/pull-requests/tutorials/roll-out-stacked-prs |

Fetch only the pages the task needs, on every invocation. Search summaries
can omit flags; for exact syntax, run
`gh stack <command> --help`, which reflects the installed extension. If docs,
`--help`, and this skill disagree, trust `--help` for syntax and the docs for
behavior, and tell the user this skill needs an update.

## Decide: stack or not

- **Stack** when a change is too big to review at once and splits into layers
  that each make sense alone, or when new work needs code from a PR that has not
  merged yet.
- **Single PR** when the change is small or one concern.
- **Wait or split instead** when the new work is independent and only touches the
  same files as an open PR. A stack merges bottom-up only, so stacking couples
  the two PRs for no gain.
- **Cannot stack**: two PRs that both depend on the same parent (stacks are
  linear), or branches across forks. Re-check both limits in the about page.

State the choice and why before acting.

## Notice stack opportunities during any git work

This skill can be pulled in mid-task (opening a PR, cutting a branch, rebasing),
where the user did not ask about stacking. Keep that path cheap: do a local
check first and load the docs only once a signal is real and the user wants to
pursue it.

Signals, all readable with plain `git` and `gh pr list`:

- The branch is cut from, or contains the head of, another branch that has an
  open unmerged PR.
- An open PR's base is a feature branch rather than the default branch. The user
  has a hand-made stack; offer to adopt it.
- The diff spans several concerns that could each be reviewed alone.
- The parent PR just merged (especially squash) and the child branch needs
  retargeting or rebasing.

With a signal, say so in one line and offer to stack. Do not take over the task
or restructure branches unasked. With no signal, or a non-GitHub remote, a
fork-based workflow, or another stacking tool already in use, carry on with the
original task and say nothing about stacking.

## Workflow

1. **Load docs** for the task, per the table above.
2. **Preflight.** Compare `gh --version` with the minimum in the quickstart, check
   `gh extension list` for `gh-stack`, and confirm the remote is the user's
   repository rather than a fork. If the extension is missing, obtain any
   installation authorization not already provided.
3. **Read state before changing it.** Run `git status`, note the current branch,
   run `gh stack view`, and list open PRs for the branches involved. If branches
   already have PRs, adopt them with the documented adopt path rather than
   recreating them, which would lose review threads.
4. **Act with the documented commands.** Local-only steps (init, add, view,
   navigation) need no confirmation. Ask first before anything that publishes or
   is hard to undo: push, submit, sync (force-pushes rebased branches), merge,
   unstack. Reuse authorization already provided for the action; do not ask
   again as part of an authorized fix-and-push workflow. Merge still requires
   an instruction naming the PR to merge.
5. **Report** the stack as bottom-to-top branches with PR links and status, and
   flag anything left for the user (drafts to mark ready, conflicts to resolve).

## Repo conventions

- **Branch names for Linear work** come from `band-linear-create-branch` (`git lb`),
  not from hand-typed names. Create the branch that way, then adopt it into the
  stack per the docs.
- **Mid-stack review comments**: fix on the branch that owns the commit, then
  use the documented cascade rebase so branches above it stay consistent.
- **Merge tooling**: any bot, script, or ChatOps merge must use the merge API the
  rollout page names. Legacy merge endpoints cannot merge a stack.
