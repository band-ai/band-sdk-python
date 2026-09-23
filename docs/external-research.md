# External Research & Code Reuse

Before implementing a nontrivial mechanism from scratch (a bounded cache,
retry/backoff, rate limiting, etc.), search the web for an existing library or
established solution first. Do not reinvent the wheel.

When evaluating a third-party package for integration, check:

- **Provenance**: official, verified, or well-known org repos (e.g. `aio-libs`)
  with clear licensing and transparent ownership over an unknown solo repo.
- **Community trust**: a strong track record, real GitHub star counts, and
  actual adoption — not just a plausible-sounding name.
- **Maintainability**: recent commit activity, steady issue resolution, and
  clear documentation. Commits still landing on `master` while the last
  tagged release sits a year-plus old is a real red flag, not noise — it means
  fixes exist that nothing you can `pip install` actually has.

Then, before committing to it: verify the specific capability you need is in
the *released* version you'd actually install, not just documented on the
project's `latest`/`master` docs — `pip install` it and exercise the real call
in this repo's venv. This is the same verify-before-relying discipline as any
other external behavior (see `AGENTS.md`), applied to library selection
specifically. Live example: `aiocache`'s docs describe a `maxsize`
bounded-eviction option that only exists on its unreleased `master` branch —
the installed `0.12.3` package raises `TypeError` on that kwarg.

If no released library both fits the actual access pattern and survives that
check, a small hand-rolled version beats a dependency that only looks like it
solves the problem.
