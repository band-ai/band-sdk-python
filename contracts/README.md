# Multi-agent skills

Reusable guidance and enforceable coordination for agents: prompts, roles,
messages, states, outcomes and deadlines. Ordinary quoted strings supply prompt
metadata for the next item; sigils are used only when needed. A skill explains how to do useful work;
its contract gives the room rules it can check. The room publishes
the applicable rules and makes the final admission decision for every agent
proposal. Any authorized agent can discover those rules and interact through
the public protocol, using its own implementation.

The remote program/LLM runner is a separate, **optional experiment**. Its client
journal, snapshots, local checker, program replacement and repair are not room
participation requirements. Read [room rules and ordinary clients](spec/room-clients-v2.md)
for the boundary, discovery flow and remaining public-host integration work.

Start with [one documented skill](spec/start-here-v2.md), then the
[multi-agent skill guidance](spec/multi-agent-skills.md),
[participant types and collections](spec/coordination.md),
[contribution outcomes](spec/contribution-outcomes.md) and [renewable clocks](spec/renewable-timeouts.md).

Core host integration is **in progress**. The [implementation ledger](IMPLEMENTATION.md)
is the current local evidence record; its latest section is last. The historical
[production ledger](PRODUCTION.md) records the earlier status of standalone
services, existing Band rooms and the optional runner.

Read the [implementation explainer](IMPLEMENTATION-EXPLAINER.md) for the system's
purpose, a contract example, execution and recovery diagrams, and qualification limits.

The [language-independent specification](spec/README.md) leads with the current
`0.2.0-lab.1` profile: [one review from scratch](spec/start-here-v2.md), the
[reviewed profile](spec/profile-v2.md) and the [DSL](spec/contract-dsl.md). The
[engine](spec/engine.md), [requirements](spec/requirements.json) and
[implementation-generation guide](spec/implementation-guide.md) complete the
specification. The normative target is separate
from the current qualification status. The [worked session](spec/walkthrough.md)
traces a complete current contract through accepted, rejected, duplicate and
deadline decisions. The [Band integration guide](spec/band-integration.md)
connects contract roles, rules and deadlines to room membership, durable
acceptance and recovery through the Band binding, which is not yet qualified
for this profile, with its own expectation ledger and remaining implementation work. The earlier
[specification improvement ledger](spec/IMPROVEMENTS.md) records the rc.2 pass.

## Source and reproducible checks

- `header-spikes/`: discovery adapters, safe YAML profile and pinned Unicode data.
- `contract-lab/`: compiler, pure engine, portable checkpoints and local checker.
- `agent-runtime/`: retained runner guide and executable examples; Python implementation is in `../src/remote_agent/`.
- `../src/band/contracts/`: public SDK clients and optional async service owners.
- `../tests/contracts/`: SDK-native contract regression tests.
- `spec/`: portable schemas, checked vectors, requirements and generation tools.
- `skills/`: authoring, iteration and deterministic-participant workflows.
- `deploy/`: self-contained engine/wheel build and isolated installation checks.

The [release guide](deploy/README.md) builds a bundle with a file manifest and
tests it from a copied location without a development checkout.

The Python implementation is distributed with `band-sdk[contracts]`, using the
canonical SDK dependency graph in the repository-root `pyproject.toml` and
`uv.lock`. The contracts extra supplies the pinned JSON Schema dependency used
by authoring and program-host operations. Linux execution also requires
bubblewrap, libseccomp and the prerequisites in
[requirements-system.json](requirements-system.json). That file records the
reference host; it does not certify a deployment. Elixir dependencies are pinned
by each project's `mix.lock`. The [public SDK guide](../docs/contracts.md) and
[API examples](../examples/contracts/README.md) cover client and service entrypoints.

From the SDK repository root:

```sh
uv sync --all-packages --locked --extra dev --extra contracts --python 3.12
```

In `contracts/header-spikes/` and `contracts/contract-lab/`, run
`mix deps.get --check-locked`, `mix compile --warnings-as-errors`, `mix test`,
and `mix format --check-formatted`. Then run the Python gates from the SDK root:

```sh
uv run --all-packages --locked --extra dev --extra contracts python -m contracts.spec.tools.checkspec
uv run --all-packages --locked --extra dev --extra contracts pytest tests/contracts --no-cov
uv build --wheel
```

[requirements-build.lock](requirements-build.lock) pins the isolated release
builder tools; it is not the runtime dependency graph. The release builder
exports `requirements-runtime.lock` from the root `uv.lock` and seals its exact
dependency wheels. The historical contract-specific development lock remains
an input record; canonical SDK checks use `uv.lock`. Do not regenerate expected
results as part of ordinary checks. Run the complete clean-source release gate
and installation checks described in [the release guide](deploy/README.md).
