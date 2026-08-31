# Dependency Conflicts

**crewai cannot coexist** with parlant or pydantic-ai in the same Python
environment due to conflicting transitive dependencies:

| Conflict | crewai requires | Other package requires |
|---|---|---|
| pydantic | `<2.13` | pydantic-ai-slim 2.x needs `>=2.12` |
| opentelemetry-sdk | `~=1.42.0` | parlant needs `>=1.37` |

This is declared in `pyproject.toml` via `[tool.uv] conflicts` so `uv lock`
resolves each in a separate fork.

**parlant cannot coexist with pydantic-ai** either, for an unrelated reason: it's a
namespace collision, not a version ceiling. `parlant` depends on the `griffe`
distribution; `pydantic-ai-slim` depends on `griffelib` — two different PyPI
distributions that both install files into the same `griffe` import path.
Installing both corrupts that path (whichever wheel's files land last wins per
file, nondeterministic by install order). Also declared via `[tool.uv] conflicts`.
Separately, `parlant` itself pulls `fastmcp` (a `griffelib` dependency as of
`fastmcp>=3.2.4`) alongside its own direct `griffe` dependency, so a `[tool.uv]
constraint-dependencies` entry pins `fastmcp>=3.2.0,<3.2.4` — otherwise parlant
collides with itself even with pydantic-ai nowhere in the picture.

**Extras layout:**
- `dev` — includes all framework deps **except** crewai and parlant
- `dev-crewai` — includes crewai + test tooling only (no parlant/pydantic-ai)
- `dev-parlant` — includes parlant + test tooling only (no crewai/pydantic-ai)
- `crewai` is mutually exclusive with `parlant` and `pydantic-ai` runtime extras
- `parlant` is mutually exclusive with `pydantic-ai` (the griffe/griffelib clash above)

**For CI:** crewai adapter tests require a separate job/step using
`uv sync --extra dev-crewai`; parlant adapter tests likewise use
`uv sync --extra dev-parlant` (`test-parlant` job).
