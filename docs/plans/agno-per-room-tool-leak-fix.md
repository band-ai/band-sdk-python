# Fix: Agno adapter contact-tool schema leaking across rooms

## Context

PR #366 adds the Agno adapter (`src/band/adapters/agno.py`). It wires Band tools
**additively** onto a single shared Agno agent via `_ensure_band_tools` +
`add_tool`, tracking `_wired_tool_names` to stay idempotent. The agent therefore
accumulates the **union** of every tool any room has ever needed. Concrete
consequence (documented in the `_ensure_band_tools` docstring): once a
contact-hub room wires the contact-management tools, those contact tool
*schemas* stay visible to the LLM in **every** room — there is no strict
per-room tool visibility. Execution is already room-correct (entrypoints route
through the `_current_tools` ContextVar), so this is purely a **visibility**
leak, but it pollutes non-contact rooms' tool lists and can nudge the model to
call contact tools where they don't belong.

Goal: give each room exactly its appropriate Band tool schemas (gated by
`Capability.CONTACTS` or `is_hub_room`) **without** removing the
`_current_tools` ContextVar and **without** per-room agent instances.

### Decisions (from user)
- **Approach:** callable-factory tools (Agno-native per-run tool resolution).
- **Where:** stack the fix **on top of PR #366's branch**
  (`feat/sdk-add-agno-adapter-python-INT-856`), but **do not merge** the fix
  into that branch or into `dev` yet — produce a reviewable diff first.
- **Workflow:**
  1. `git fetch origin feat/sdk-add-agno-adapter-python-INT-856`.
  2. Point the assigned working branch `claude/tools-per-room-handling-gj1ush`
     at the PR branch head (it currently tracks `dev` and has no agno code, so
     rebase/reset it onto `origin/feat/sdk-add-agno-adapter-python-INT-856`).
     The fix then sits as commits stacked on the PR.
  3. Implement the fix; commit to the working branch.
  4. **Review gate:** show `git diff origin/feat/sdk-add-agno-adapter-python-INT-856`
     so the user sees only the fix. Do not merge into the PR branch or `dev`.
  5. Push the working branch only after the user approves the diff (per repo
     rules, push to `claude/tools-per-room-handling-gj1ush`).

## Why this works (verified against installed Agno 2.5.x)

- `Agent.arun()` has **no** `tools=` param; tools are bound to the agent
  instance. But Agno supports **callable-factory tools**: if `agent.tools` is a
  callable, Agno resolves it **per run** into `run_context.tools` and uses that
  for the model (`agno/agent/_tools.py: get_tools/aget_tools` →
  `agno/utils/callables.py: aresolve_callable_tools` → `get_resolved_tools`
  returns `run_context.tools` in preference to `agent.tools`).
- This is **concurrency-safe**: the factory result goes into the per-run
  `run_context`, never mutating shared instance state — so concurrent rooms
  (each its own `ExecutionContext`/asyncio task, `runtime/execution.py:492`)
  don't clobber each other.
- Resolution is **cached** by `_compute_cache_key` (priority:
  `callable_tools_cache_key` > `user_id` > `session_id`). Both
  `cache_callables` and `callable_tools_cache_key` are real `Agent.__init__`
  fields. We will set `cache_callables=False` to force per-run resolution
  regardless of `session_id_factory` (the adapter already caches the built
  `Function` lists itself, so Agno-side caching adds nothing).

## Changes — `src/band/adapters/agno.py`

### 1. `on_started` — install the per-run factory
After `self._agent = self._agent_factory()`:
- **Capture the developer's own tools** so the factory can re-include them
  (replacing `agent.tools` with our factory would otherwise drop them):
  - `agent.tools` is a list / Toolkit-list → `self._developer_tools = list(...)`,
    `self._developer_tools_factory = None`.
  - `None` → `self._developer_tools = []`, factory `None`.
  - Already a callable factory (developer used Agno's own per-run tools),
    detected via `is_callable_factory` (from `agno.utils.callables`) →
    `self._developer_tools_factory = <the callable>`, `self._developer_tools = []`.
    **No warn/skip** — our factory resolves it per run (see §2).
- `self._agent.cache_callables = False`  # force per-run resolution
- `self._agent.tools = self._resolve_room_tools`  # install factory
- Keep `_inject_band_instructions()` exactly as today.

### 2. New `_resolve_room_tools` factory (async)
The adapter always runs via `arun` (async path), so Agno resolves tools through
`ainvoke_callable_factory`, which can `await` an async factory. Making ours
`async` lets it transparently compose **both sync and async** developer
factories by delegating to Agno's own resolver helpers:
```
async def _resolve_room_tools(self, run_context=None) -> list:
    # developer tools, resolved with the same Agno semantics (signature
    # injection of agent/run_context/session_state, async-aware)
    if self._developer_tools_factory is not None:
        dev = await ainvoke_callable_factory(
            self._developer_tools_factory, self._agent, run_context
        )
        dev = list(dev) if dev else []
    else:
        dev = list(self._developer_tools)

    active = _current_tools.get()
    if active is None:
        return dev                                # defensive: no band tools
    include_contacts = (
        Capability.CONTACTS in self.features.capabilities
        or bool(getattr(active, "is_hub_room", False))
    )
    band = self._band_tools_cache.get(include_contacts)
    if band is None:
        band = self._build_band_tools(active, include_contacts=include_contacts)
        self._band_tools_cache[include_contacts] = band
    return [*dev, *band]                           # must return a list
```
- Reads the **same** `_current_tools` ContextVar that `_bind_room_tools` sets
  around `arun`, so the factory sees the active room.
- Reuses `_band_tools_cache` / `_build_band_tools` unchanged.
- Reuses Agno's `ainvoke_callable_factory` (from `agno.utils.callables`) so the
  developer's factory gets exactly the run-context injection Agno would give it.
  Because we replaced `agent.tools` with our factory and stored the developer's
  factory separately, Agno resolves only **our** factory — the developer factory
  is invoked exactly once per run, by us (no double resolution).

### 3. Remove now-dead union-wiring machinery
- Delete `_ensure_band_tools` and the `self._ensure_band_tools(tools)` call in
  `on_message`.
- Delete `_wired_tool_names` (instance attr + all refs).
- **Keep**: `_current_tools`, `_bind_room_tools`, `_make_band_entrypoint`,
  `_band_tools_cache`, `_build_band_tools`, `_run_agent`'s
  `with _bind_room_tools(tools): await agent.arun(...)`, the `agent` property,
  and `@_with_agent`.

### 4. Untouched
`on_cleanup` (drops `_message_history`), history handling, reply/thoughts/
execution reporting, instruction injection.

## Tests — `tests/adapters/test_agno_adapter.py`

Replace assertions tied to union-accumulation / `_wired_tool_names` with
per-room-visibility tests (use the fake tools' `is_hub_room`, see
`src/band/testing/fake_tools.py:52`):
- **Non-contact room:** `_resolve_room_tools` (under `_bind_room_tools(tools)`)
  excludes contact tools, includes developer tools + base/memory tools.
- **Hub room (`is_hub_room=True`):** includes contact tools.
- **Leak regression (core):** resolve once for a hub room, then resolve for a
  non-contact room → second result excludes contact tools. This is the bug
  being fixed.
- **Developer tools preserved:** present in both resolutions.
- **Execution still room-correct:** a band tool entrypoint executes against the
  ContextVar-bound room's `AgentTools` (unchanged behavior).
- **Developer callable-factory tools:** an agent constructed with a callable
  `tools=` factory (sync and async variants) → its tools are resolved and
  appear alongside the room's band tools in every resolution.
- Optional: drive `agent.arun` with a stub model and assert the schemas handed
  to the model are room-correct, confirming the factory is actually invoked
  per-run with `cache_callables=False`.

Update any conformance/registry expectations only if they referenced the
removed methods (they shouldn't — `tests/framework_configs/` mocks constructor
args).

## Follow-up rename: "developer tools" → "user tools"

The core fix is already implemented and committed (`36636ce`). Remaining work is a
naming pass only — rename the "developer tools" concept to "user tools" (the
tools the user configured on their own Agno agent, as opposed to Band-injected
tools). No behavior change.

In `src/band/adapters/agno.py`:
- `self._developer_tools` → `self._user_tools`
- `self._developer_tools_factory` → `self._user_tools_factory`
- `_capture_developer_tools` → `_capture_user_tools` (and its call in `on_started`)
- `_resolve_developer_tools` → `_resolve_user_tools` (and its call in
  `_resolve_room_tools`)
- local var `dev` in `_resolve_room_tools` → `user_tools`
- Update docstrings/comments: "developer's own tools" → "the user's own tools";
  keep one clarifying note that "user" here means the user who configured the
  Agno agent (to disambiguate from chat end-users / `sender_type="User"`).

In `tests/adapters/agno/test_adapter.py`:
- `test_developer_tools_are_reincluded` → `test_user_tools_are_reincluded`
  (rename the `dev_tool` local to `user_tool` and update the comment).

Leave the class/module docstrings that say "developer-built Agno agent" /
"the developer owns the agent" as-is — those describe ownership, not the tool
attribute, and remain accurate.

Verification after rename: `uv run pytest tests/adapters/agno/ -q`, then
`uv run ruff check . && uv run ruff format . && uv run pyrefly check
src/band/adapters/agno.py`. Grep for residual `developer_tools` to confirm none
remain in the adapter/tests.

## Verification

```bash
# working branch is stacked on origin/feat/sdk-add-agno-adapter-python-INT-856
uv run pytest tests/adapters/test_agno_adapter.py -v      # incl. leak regression
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v
uv run ruff check . && uv run ruff format . && uv run pyrefly check
# optional, needs live platform + LLM keys:
# E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -k agno -v -s --no-cov

# review gate: surface ONLY the fix diff for the user, do not merge
git diff origin/feat/sdk-add-agno-adapter-python-INT-856
```

Manual sanity: an agent in CONTACTS-disabled mode joined to both a normal room
and the contact-hub room should expose contact tools only in the hub room, and
exposure in the normal room must not appear after the hub room has run.

## Risk notes
- Ties to Agno's callable-factory contract (`agent.tools` callable resolved
  per-run; `cache_callables`, `get_resolved_tools` precedence,
  `ainvoke_callable_factory` signature injection). Verified on 2.5.x; re-verify
  against the `agno` version pinned on the PR branch.
- Verify `is_callable_factory` accepts an **async bound method** as a factory
  and that the async run path awaits it (it uses `ainvoke_callable_factory`).
  The adapter only calls `arun` (async), so the async factory path is always
  taken; if any sync resolution path is ever hit, an async-only factory would
  need a sync fallback.
