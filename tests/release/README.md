# Release verification

The ordinary unit matrix covers parsed metadata and the offline wheel resolver.
A separate required CI job exercises Release Please's pinned candidate builder.
Its npm dependency matches the version bundled in the pinned Release Please
Action; update both together when changing that Action.

```bash
npm ci --prefix tests/release/candidates --ignore-scripts
uv run --all-packages pytest tests/release -q --no-cov
```

The Node fixture uses the real Python strategy, linked-version plugin and merge
updaters. Only GitHub file reads are doubled. It never accesses GitHub or publishes.
