# Vetting a third-party library

Used by Phase 4 for each serious candidate. Every item cites its evidence
(release page, issue tracker, tagged source file).

- **Maintenance:** recent releases, issue and PR responsiveness, more than one
  maintainer, real adoption.
- **Fit:** license, Python 3.11+ support, typing, async support where the
  calling code is async, dependency weight.
- **Lock compatibility:** the isolated development extras described in
  `AGENTS.md` "Dependency Conflicts" (and `[tool.uv] conflicts` in
  `pyproject.toml`) mean a new dependency can break a lane. Check the
  candidate's own pins against each extra it would join.
- **The capability itself:** read it in the tagged source of the release you
  would pin, not in docs for `main`. If it can't be confirmed without running
  it, report it as unconfirmed with the call that would confirm it; Phase 5's
  Approach records it.
