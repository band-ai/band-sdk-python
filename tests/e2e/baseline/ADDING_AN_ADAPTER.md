# Wiring a New Adapter into the Matrixed E2E Tests

You just added `src/band/adapters/<framework>.py` (a `SimpleAdapter` subclass) and
its converter. This guide is the **manual** part: registering it so the baseline
matrix (`tests/e2e/baseline/`) builds, gates, runs, and reaps it automatically.

Once registered, every existing matrix scenario (`matrix_agent` / `@across_adapters`)
and the smokes run against your adapter for free — you write **no per-adapter test**
to get baseline coverage.

> The discovery guard (`test_adapter_registry.py`) **fails loudly** the moment a new
> non-bridge module appears under `src/band/adapters/` without a matching enum member
> *and* builder. A red guard is the system telling you which of the steps below is
> missing — it is not optional.

---

## TL;DR — the decision

**Is your adapter an LLM agent that runs the Band tool loop** (replies to messages
in a room), or a **bridge** that exposes Band to another protocol (a2a, acp, slack)?

| It is… | Do this |
|---|---|
| an LLM-agent adapter | **Register it** — Steps 1–3 below (and 4 if it needs new config). |
| a protocol bridge / needs a live server with bespoke setup (like `parlant`) | **Deny it** — add its module name to `NON_AGENT_ADAPTERS` in `toolkit/adapters.py`. One edit, done. |

Everything below is the register path.

---

## `NON_AGENT_ADAPTERS` — excluding a bridge

Some `band.adapters` modules are **not** LLM-agent adapters: protocol bridges
(`a2a`, `a2a_gateway`, `acp`, `slack`) expose Band to another protocol rather than
running the tool loop, and `parlant` needs a bespoke live server. They live in the
`NON_AGENT_ADAPTERS` frozenset in `toolkit/adapters.py`; `discovered_agent_ids()`
subtracts them from the folder scan, so the guard neither expects a builder nor an
enum member for them.

To exclude a new non-agent module, add its module name to `NON_AGENT_ADAPTERS` —
one edit, and the discovery guard stops demanding registration for it.

---

## Step 1 — Add the `Adapter` enum member

File: `tests/e2e/baseline/toolkit/adapters.py`

The member **value must equal the module name** under `band.adapters` (the guard
keys on this three-way: enum ⇔ registry ⇔ discovered module).

```python notest
class Adapter(StrEnum):
    ...
    MYFRAMEWORK = "myframework"   # == src/band/adapters/myframework.py
```

## Step 2 — Add the `@adapter` builder

Same file, in the builder section at the bottom. The builder turns
`BaselineSettings` (+ a steering `prompt`, `features`, and `tools`) into a
**ready-to-run** adapter instance. It is the seam that hides each framework's
heterogeneous constructor.

```python notest
@adapter(Adapter.MYFRAMEWORK, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
def _build_myframework(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    # Lazy-import the framework INSIDE the builder so importing this module
    # (which triggers registration) never pulls in an absent optional dep.
    from band.adapters.myframework import MyframeworkAdapter

    return MyframeworkAdapter(
        model=s.llm_models.openai_model,
        prompt=prompt,            # map to whatever arg your framework uses:
                                  # prompt / custom_section / system_prompt / instructions
        additional_tools=_custom_tool_defs(tools),  # translate ToolSpec -> CustomToolDef; or _reject_tools(...) — see Step 5
        features=features,
    )
```

Rules for the builder — each exists for a reason, don't skip them:

- **Lazy import inside the function.** Registration happens at import time; if the
  import were top-level, importing `adapters.py` (which the *whole* matrix does)
  would crash whenever your optional dep is absent. Inside the builder, the import
  only runs when a test actually builds your adapter.
- **`requires=[...]`** — the `Dep` members your adapter needs (provider key, CLI,
  server, dependency lane). A missing one **fails** that matrix cell with the
  reason; it never silently skips. Every spec must declare at least one requirement
  (`test_every_spec_requires_dep_members` enforces it). **`requires` also decides
  your CI lane** — see "Which CI lane your adapter lands in" below.
- **`supports=[...]`** — the platform `Capability`s your adapter can expose
  (`Capability.MEMORY`, `Capability.CONTACTS`). Most tool-loop adapters use the
  `_LLM_TOOL_LOOP` shorthand (`= (MEMORY, CONTACTS)`). This only declares what
  capability-scoped matrices (`@across_adapters(supports={Capability.MEMORY})`) can
  *select* — it does **not** turn the capability on. The test enables it by passing
  matching `features`. If your adapter doesn't do the tool loop (e.g. a terminal
  flow), omit `supports` so it advertises nothing.
- **Map `prompt`** to whichever constructor argument steers your framework's system
  prompt. The matrix passes one generic `prompt`; you route it.
- **Thread `features` through** so tests can flip memory/contacts/execution-emission.

### Which CI lane your adapter lands in

You **don't set a lane on the adapter** — it's derived from `requires`. Each `Dep`
names a lane (`dep_lane` in `requirements.py`); your adapter runs in the unique
non-default lane among its deps, else the shared `core` lane. So `requires` is the
one and only place you express lane association:

| `requires` includes… | Lane | `uv` extra |
|---|---|---|
| only a provider key (`Dep.OPENAI` / `Dep.ANTHROPIC`) | `core` | `dev` |
| `Dep.GOOGLE` | `google` | `dev` |
| `Dep.LETTA` | `letta` | `dev` |
| `Dep.CODEX_CLI` / `Dep.OPENCODE_SERVER` | `backends` | `dev` |
| `Dep.CREWAI` | `crewai` | `dev-crewai` |

An adapter belongs to exactly one lane: two deps naming different non-default lanes
is an error (`adapter_lane` raises). If your adapter needs a **brand-new** lane (its
own venv, server, or isolation — e.g. provider rate-limit flakiness), don't invent
it here — follow **"Adding a CI lane"** in `README.md`: add the `Lane` + `Extra` and
point a `Dep` at it in `requirements.py`, then `requires=[that Dep]` here.

## Step 3 — Run the discovery guard (it should now pass)

```bash
E2E_TESTS_ENABLED=true uv run pytest \
  tests/e2e/baseline/guards/test_adapter_registry.py -v -s --no-cov
```

`test_registry_covers_discovered_adapters` going green means enum ⇔ registry ⇔
module are in sync. This test constructs nothing, so it runs in any lane with no
provider keys.

---

## Step 4 — Only if your adapter needs config the toolkit doesn't have yet

Most adapters reuse an existing `Dep` (OpenAI/Anthropic/Google key) and existing
model/credential settings — if so, **skip this step**. Add config only for a genuinely
new prerequisite.

### 4a. A new requirement → `Dep` + a check

File: `tests/e2e/baseline/toolkit/requirements.py` (pytest-free — that's deliberate,
so the registry can reference `Dep` without importing pytest).

1. Add a `Dep` enum member.
2. Add a `_DEPS[Dep.X] = DepSpec(...)` entry — one record holding the dep's
   `available` predicate (a pure function of `settings`/`os.environ` returning
   `bool`), the `reason` shown when the cell **fails** (name the exact env var /
   CLI / server), and — only if the dep gates its own CI lane (a different venv,
   an external backend, or isolation) — `lane=` (a `Lane` member, mapped to its
   `uv` extra in `LANE_EXTRAS`). A provider-key dep with no isolation need rides
   the shared `core` lane (`DEFAULT_LANE`), so it needs no `lane`. See
   `_codex_cli_available`, `_letta_available` for CLI-on-PATH and server-URL
   predicates.

```python notest
class Dep(Enum):
    ...
    MYPROVIDER = "myprovider"

_DEPS = {
    ...
    Dep.MYPROVIDER: DepSpec(
        lambda s: bool(s.llm_credentials.myprovider_api_key),
        "MYPROVIDER_API_KEY not set",
    ),
}
```

### 4b. A new credential / model id → `settings.py`

File: `tests/e2e/baseline/settings.py`. Add the field to the concern that owns it:

- a **provider API key** → `LLMCredentials` (no env prefix; standard provider var
  name, e.g. `myprovider_api_key: str = ""  # MYPROVIDER_API_KEY`).
- a **model id** → `LLMModels` (env-prefixed `E2E_`, e.g.
  `myprovider_model: str = "..."  # E2E_MYPROVIDER_MODEL`).

CLI/server prerequisites that aren't keys (a binary on PATH, a base URL) are read
straight from `os.environ` inside the builder and the check — they don't need a
settings field (see codex/opencode/letta).

### 4c. Document the new env vars

Add them to the **Environment Variables** section of the root `CLAUDE.md` so the
next developer/agent knows the knob exists.

---

## Step 5 — Custom tools: accept or reject (no silent drop)

The matrix can pass `tools=[CustomToolDef, ...]` (from `@with_agents(..., tools=[...])`).

- If your framework accepts band `CustomToolDef`s, forward them as
  `additional_tools=tools`.
- If your framework takes tools in a different form, translate the `ToolSpec`:
  agno uses `t.as_callable()` (a plain callable), pydantic-ai uses
  `t.as_callable(ctx_annotation=RunContext)` (a `RunContext`-first callable).
- If it genuinely **can't** accept a locally-defined tool (only letta today —
  its tools live on an MCP server), call `_reject_tools(Adapter.MYFRAMEWORK, tools)`
  at the top of the builder. This **fails loudly** when a test asks for tools your
  adapter can't honor, instead of
  silently dropping them (which would be a false green).

---

## Step 6 — Verify the cell actually runs (needs the backing key/CLI)

With your `Dep`'s requirement satisfied in the environment, run a matrix smoke
scoped to your adapter:

```bash
E2E_TESTS_ENABLED=true uv run pytest \
  tests/e2e/baseline/smoke/matrix/test_adapter_matrix.py -k myframework -v -s --no-cov
```

Then the capability matrix if you declared `supports`:

```bash
E2E_TESTS_ENABLED=true uv run pytest \
  tests/e2e/baseline/smoke/matrix/test_capability_matrix.py -k myframework -v -s --no-cov
```

A cell that's **red because its key/CLI/server is absent** is intended — no single
environment turns the whole matrix green (the `crewai` lane needs the `dev-crewai`
extra; codex/opencode/letta need their backends). "Red" there means "this backend isn't
wired up in this environment", not "the wiring is wrong". A red *discovery guard*,
by contrast, always means a missing enum member or builder.

---

## Checklist

- [ ] **Step 1** — `Adapter.<NAME> = "<module_name>"` (value == module file name).
- [ ] **Step 2** — `@adapter(...)` builder with lazy import, `requires=` (also sets
      the CI lane), `supports=`, mapped `prompt`, threaded `features`, and `tools`
      handled (forward or reject).
- [ ] **Step 3** — `test_adapter_registry.py` green (guard satisfied).
- [ ] **Step 4** *(only if new config)* — new `Dep` + `_DEPS` entry; new
      credential/model field in `settings.py`; env vars documented in `CLAUDE.md`.
- [ ] **Step 5** — custom tools forwarded or `_reject_tools(...)`.
- [ ] **Step 6** — adapter (and capability) matrix smoke green with the backend present.
- [ ] `uv run ruff check . && uv run ruff format . && uv run pyrefly check`.

## What you do NOT touch

The matrix machinery is generic — leave it alone. You do **not** edit `agents.py`
(`@with_agents` / `@across_adapters`), `conftest.py` fixtures, `capture.py`,
`provisioning.py`, or any existing scenario/smoke. Registering in `adapters.py`
(+ optional `requirements.py`/`settings.py`) is the entire surface. If you find
yourself editing a scenario to special-case your adapter, that's a smell — the
adapter should conform to the generic builder contract instead.

## Reference: the files in play

| File | Your edit |
|---|---|
| `toolkit/adapters.py` | **always** — enum member + `@adapter` builder (or `NON_AGENT_ADAPTERS` entry for a bridge) |
| `toolkit/requirements.py` | only for a brand-new `Dep` + availability check |
| `settings.py` | only for a new credential or model id field |
| `CLAUDE.md` (root) | document any new env vars |
| `guards/test_adapter_registry.py` | nothing — it's the guard that grades your work |

See `tests/e2e/baseline/README.md` for how tests *use* the matrix once you've
registered the adapter.
