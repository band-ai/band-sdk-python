# Sandbox kit launcher

Boots a customer's Python workspace as a Band agent inside a
[`band-python-kit`](../../../../docker/band_python_kit/README.md) Docker
Sandbox. The kit's startup command runs it headlessly on every sandbox
start — after the base image entrypoint has installed the proxy CA and
dropped to the non-root agent user:

```bash
$BAND_SDK_PYTHON -m band.docker.launcher
```

It is not an API for application code: customers configure it entirely
through the workspace's `band.yaml` (see the kit README and the
[example workspace](../../../../docker/band_python_kit/example/) for the
authoring guide); the SDK imports nothing from it.

## What it does

1. **`config`** — read `band.yaml` (strict: unknown fields fail) and the
   documented environment overrides. Precedence: env → `band.yaml` →
   production defaults.
2. **`paths`** — resolve and fence every configurable path: workspace
   paths must stay inside the workspace (symlink escapes fail), runtime
   paths (venv, state, cache, logs) must live outside both the mounted
   workspace and the immutable SDK home.
3. **`credentials`** — optionally fill missing keys from the opt-in
   workspace env file, after the `GUARDS` checklist in
   [`credentials.py`](credentials.py) passes. The process environment
   always wins; values are never logged.
4. **`repo`** — optionally materialize the project from Git (reuses
   `band.docker.repo_init`: clone-or-validate under a file lock, optional
   context indexing). The clone destination is always the fenced project
   path — never configured separately — and must be a workspace
   subdirectory; state/context live under the runtime state path.
5. **`sync`** — `uv sync --locked` with the image's pinned uv into a
   sandbox-owned venv, serialized under a file lock. No lockfile, no
   launch — resolution never happens at startup.
6. **`run`** — assemble the above into a `ResolvedLaunch`, then
   `os.execve` the customer entrypoint with the customer venv's
   interpreter, so signals reach customer code directly.

Every failure is a `LaunchError` naming its phase, e.g.
`[credentials] credentials file must be gitignored: …`. No error, log
line, or diagnostic ever contains secret values.

```python
from band.docker.launcher import LaunchError, ResolvedLaunch, resolve_launch

assert callable(resolve_launch)
assert issubclass(LaunchError, ValueError)
assert "credentials" in ResolvedLaunch.model_fields
```

## Module map

| Module | Concern |
|---|---|
| `config.py` | `band.yaml` models (strict) + `LauncherEnv` overrides |
| `paths.py` | path resolution and fencing rules |
| `credentials.py` | opt-in secrets file and its guard checklist |
| `bootstrap.py` | optional repository bootstrap via `repo_init` |
| `sync.py` | locked dependency sync via the pinned uv |
| `run.py` | phase assembly, child environment, exec, `main()` |
| `launch.py` | `ResolvedLaunch` — the model the phases hand around |
| `errors.py` | `LaunchError` (phase-attributed) |

Tests live in `tests/docker/launcher/` (one file per concern), with the
kit-drift tests in `tests/docker/test_kit_spec.py`.
