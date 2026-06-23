# Adding a new adapter or bridge

This guide is the **registry checklist**: every place you must touch when adding
a framework adapter (or a protocol bridge), and — crucially — **which fail-closed
test catches you if you forget each one**. Most of these registries are guarded
by drift gates, so the cost of forgetting is a red CI run, not a silent gap.

For the *source-file* TDD workflow (scaffolding the adapter + converter classes,
implementing `convert`/`on_message`, framework-specific unit tests), see
**CLAUDE.md → "Adding a New Framework Integration"**. This document is the
companion that covers the config / conformance / E2E registries.

Throughout, `<id>` is the lowercase adapter id, which **must equal the module
filename** in `src/band/adapters/` (e.g. `langgraph.py` → `"langgraph"`). That
filename is the single source of truth the drift gates discover from disk.

---

## A. Adding a framework adapter

A "framework adapter" runs a model loop and dispatches platform tools
(LangGraph, Anthropic, Gemini, Codex, Letta, …). Work top-to-bottom.

### 1. Production source

| # | File | What to add |
|---|---|---|
| 1 | `src/band/adapters/<id>.py` | Adapter class extending `SimpleAdapter[H]`; declare `SUPPORTED_EMIT` and `SUPPORTED_CAPABILITIES` ClassVars. **Guard optional deps** with a lazy import or `try/except` flag — do *not* hard-import the framework at module top (Gemini's eager `ImportError` is the lone exception and is special-cased everywhere). |
| 2 | `src/band/converters/<id>.py` | History converter (`{Id}HistoryConverter`) **if** the adapter converts message history. Metadata-only adapters (codex/letta/opencode) skip this — they reconstruct state from task events. |
| 3 | `src/band/adapters/__init__.py` | Add the `__getattr__` name→class branch (lazy import). ⚠️ *No drift gate covers this hand-maintained chain — don't forget it.* |
| 4 | `pyproject.toml` | Add the optional-dependency extra under `[project.optional-dependencies]`, and include it in `dev` (unless it conflicts — see crewai). |

### 2. Conformance config registries

| # | File | What to add | Drift gate if forgotten |
|---|---|---|---|
| 5 | `tests/framework_configs/adapters.py` | `_build_<id>_config()` returning `AdapterConfig(framework_id="<id>", …)`; append to `_ADAPTER_CONFIG_BUILDERS`. | `test_config_drift.py` fails: a module on disk with no config that isn't in `ADAPTER_EXCLUDED_MODULES`. |
| 6 | `tests/framework_configs/converters.py` | `_build_<id>_config()` → `ConverterConfig`; append to `_CONVERTER_CONFIG_BUILDERS`. Metadata-only → add to `CONVERTER_EXCLUDED_MODULES` instead. | converter conformance / config drift. |
| 7 | `tests/framework_configs/output_adapters.py` | An output adapter matching the converter's output shape (`BaseDictListOutputAdapter` / `StringOutputAdapter` / …). | converter conformance. |
| 8 | `tests/framework_conformance/injection_registry.py` | One `InjectionBinding` in `INJECTION_BINDINGS`: either an **honest seam** (`family`, `seam`, `spike_test`, `observation_paths`, `version_pin` if `drift_risk=HIGH`) or **`tier1_status=N_A_TIER2`** with `na_subreason` + a `tier2_coverage` path that exists. | `test_injection_binding_drift.py` (fail-closed): no binding + not excluded; renamed/missing seam (AST-checked); HIGH drift without `version_pin`; N-A without `tier2_coverage`. Honest bindings also need `test_injection_canary.py` coverage. |
| 9 | `tests/framework_conformance/baseline_applicability.py` | An `AdapterApplicabilityProfile` in `_REQUEST_PROFILES` (`request_read_status`, `capture_family`, `base_instruction_surface`). | unknown-pair → `UNKNOWN_FAIL_CLOSED` in the scorecard tests. |

### 3. Tier-1 request/dispatch capture (honest-seam adapters only)

| # | File | What to add | Drift gate |
|---|---|---|---|
| 10 | `tests/framework_conformance/request_capture.py` | A `RequestCaptureProbe` in `REQUEST_CAPTURE_PROBES` + a `capture_<id>_request()` returning a normalized `CapturedRequest`. | L0–L2 conformance rows for the adapter. |
| 11 | `tests/framework_conformance/dispatch_capture.py` | An `elif adapter_id == "<id>"` branch in `dispatch_tool()` (and `dispatch_l1_custom_tool()`) installing the scripted decision at the declared seam. | L0/L1 dispatch rows. |
| 12 | `tests/framework_conformance/test_<id>_injection_spike.py` | A runnable, no-secrets spike proving real-routing dispatch + a negative control (for honest families). | `test_injection_canary.py` requires a canary builder for each honest binding. |

### 4. Live E2E registries (`tests/e2e/`)

| # | File | What to add | Drift gate if forgotten |
|---|---|---|---|
| 13 | `tests/e2e/adapters/conftest.py` | `create_<id>_adapter(settings)` and/or `create_baseline_default_<id>_adapter(settings)`, **decorated** with `@adapter_factory("<id>", groups=(...))`. Pick groups: `"default"` (general smoke), `"baseline_default"` (L1–L4 unsteered), `"baseline_l0"`. The `ADAPTER_FACTORIES` / `BASELINE_DEFAULT_*` / `BASELINE_L0_*` dicts assemble themselves from these decorators — **there is no list to edit.** | `test_default_factory_group_covers_every_non_bridge_adapter`, `test_baseline_default_factory_group_covers…`, `test_baseline_l0_adapter_matrix_covers…` all fail if the adapter is in neither a group nor an exclusion map. |
| 13a | (same file) | If the adapter is intentionally **not** in a group, add it to the matching `DEFAULT_GROUP_EXCLUSIONS` / `BASELINE_DEFAULT_GROUP_EXCLUSIONS` (with a reason string) or `BASELINE_L0_BLOCKED_ADAPTER_NAMES`. | same gates as 13. |
| 14 | `tests/e2e/baseline_artifacts.py` | Add `<id>` to `_PROVIDER_USAGE_SUPPORTED_ADAPTERS` **only if** it exposes provider-owned input/output token usage. Otherwise it is auto-classified as provider-usage-blocked (fail-safe default). | `test_provider_usage_baseline_matrix_covers…`. |
| 15 | `tests/e2e/settings_groups.py` / `baseline_settings.py` | **Only if** the adapter needs new runtime config (its own env-prefixed group like Codex/OpenCode/Letta) or live companion-agent identities. Most adapters need nothing here — they use the shared provider-credential groups + platform settings. | n/a |

> **Tip:** run `uv run pytest tests/framework_conformance tests/e2e/test_e2e_hygiene.py -q` after wiring up. The drift gates will list exactly which registry still misses the new id (`uncovered_new_adapters: [...]`).

---

## B. Adding a protocol bridge (A2A / ACP / Slack-style)

Bridges (`a2a`, `a2a_gateway`, `acp`, `slack`) **wrap** another agent or speak a
different protocol; they have no model→tool dispatch contract, so they are
**excluded** from the conformance suites rather than registered. For a new bridge
module `src/band/adapters/<id>.py`, add its id to the exclusion sets (each with a
one-line rationale) — the drift gates *require* every on-disk module to be either
registered or explicitly excluded:

| File | Set to add `<id>` to |
|---|---|
| `tests/framework_conformance/injection_registry.py` | `INJECTION_EXCLUDED_MODULES` |
| `tests/framework_configs/adapters.py` | `ADAPTER_EXCLUDED_MODULES` |
| `tests/framework_configs/converters.py` | `CONVERTER_EXCLUDED_MODULES` (if it ships a converter module) |
| `tests/e2e/adapters/conftest.py` | the `*_GROUP_EXCLUSIONS` maps **only if** the bridge module isn't already filtered by `INJECTION_EXCLUDED_MODULES` (the E2E discovery subtracts bridges via that set, so usually nothing is needed here) |

`baseline_applicability.applicability_for()` then resolves the bridge to
`EXCLUDED_BRIDGE` automatically.

---

## C. The drift-gate safety net (what fails if you forget)

| Forgot to… | …this test goes red |
|---|---|
| register an `AdapterConfig` (or exclude the module) | `tests/framework_conformance/test_config_drift.py` |
| add an `InjectionBinding` (or exclude); rename a declared seam; pin a HIGH-drift version; point `tier2_coverage` at a real file | `tests/framework_conformance/test_injection_binding_drift.py` |
| supply a canary for an honest binding / keep the seam routing | `tests/framework_conformance/test_injection_canary.py` |
| put the adapter in an E2E factory group or an exclusion map | `tests/e2e/test_e2e_hygiene.py::test_*_factory_group_covers_every_non_bridge_adapter` |
| cover the adapter in the L0 / provider-usage matrices | `tests/e2e/test_e2e_hygiene.py::test_baseline_l0_adapter_matrix_covers…` / `…provider_usage_baseline_matrix…` |
| review an (adapter × scenario) pair | the scorecard tests (`UNKNOWN_FAIL_CLOSED` is not a passing status) |

All of these discover the adapter universe from `src/band/adapters/*.py` on disk
(minus `INJECTION_EXCLUDED_MODULES`), so simply **creating the module file** is
enough to make the gates demand that you finish wiring it up.
