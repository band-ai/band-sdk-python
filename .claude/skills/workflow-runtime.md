# Running project workflows across agents

Read this when a workflow skill links here. The workflow defines the work;
the host supplies the tools. Use only capabilities actually available in
the current session, and follow its permissions and repository instructions.

## Skills and tools

- Read a referenced project's `SKILL.md` and the relevant supporting files.
  A skill-invocation tool is optional; reading the instructions works too.
  Resolve Markdown links relative to their file and repository paths from
  the repository root.
- Discover connected tools before assuming a service or method is missing.
  Verify the current schema or CLI help before calling it. Use the host's
  search, fetch, shell, and file-editing tools rather than inventing aliases
  for another agent's APIs.
- Ask through the host's question tool when available, or in ordinary chat.
  A required answer must arrive before dependent work proceeds. Existing
  authorization carries forward; do not repeat an answered approval question.
- A workflow does not expand the user's scope. Keep local review local
  unless publication is authorized. Loading another skill is not permission
  to post reviews, change tickets, push branches, or merge a PR.
- When creating or preparing a PR, follow `AGENTS.md` "PR Lifecycle and
  Readiness Review": start as draft and promote only after the author review
  passes. Planning, early feedback, and a cheap readiness signal do not
  establish implementation or validation completion.

## Delegation and ownership

- Parallel roles describe independent work, not a required API or model.
  Use the available delegation tools within the host's concurrency limit;
  schedule excess roles in later batches. Inherit the session model unless
  the user requests another available model.
- Give each worker its objective, scope, inputs, permitted side effects, and
  expected evidence. Use a fresh context when supported and sufficient;
  otherwise provide a self-contained brief without assuming context isolation.
- If delegation is unavailable or disallowed, perform the roles as separate
  sequential passes. Preserve the verification criteria, and report that
  independent review was unavailable rather than claiming it happened.
- Reviewers read shared checkouts without editing, switching refs, or
  publishing. Assign implementation to one writer per file. Experiments
  that modify source, dependencies, or Git state need isolated worktrees and
  environments, or must run serially under one owner. Never revert another
  worker's or the user's changes.
- A handoff has an explicit result and next action. Use the host's resume
  or follow-up mechanism; if it cannot resume a worker, pass its saved
  findings to a new worker or continue inline. A verification pause is not
  the end of an already-authorized fix task.

## Plans and scratch files

- A planning-only task stays planning-only. Use native plan mode if
  available; otherwise enforce that boundary explicitly without claiming
  the host changed modes. Never bypass restrictions imposed by native mode.
- Use the host's plan file when provided. Otherwise choose one writable
  scratch file outside tracked product files, record its path, and revise
  that same file throughout. If writing is prohibited, keep the draft in
  the conversation and report that it was not saved.
- Plan finalization authorizes only the publication agreed with the user;
  it does not start implementation. If the current mode blocks an agreed
  publication, preserve the final draft and report the blocked operation.

## Completion and cleanup

Track workers, processes, temporary worktrees, and scratch files created by
this run. Await completion, or cancel by their recorded handles and confirm
they stopped, before removing resources they may still use. Never kill by
process name, delete a shared environment, or remove a user's plan as cleanup.
Report incomplete checks and unavailable capabilities explicitly.
