"""Fail-closed drift gate for the Tier-1 ``InjectionBinding`` registry.

Extends the config-drift idea (``test_config_drift.py``) to Tier-1 conformance:
a new adapter cannot silently skip Tier-1 participation. Every adapter module is
either bound (with a declared family + status) or explicitly excluded; honest
bindings must point at a real seam and a real spike; N-A bindings must record a
reason and a resolvable Tier-2 test; HIGH-drift bindings must carry a version pin.

See ``docs/baseline-conformance/tier1-injection-contract.md`` §5.6.
"""

from __future__ import annotations

import ast
import importlib.metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from tests.framework_conformance.injection_registry import (
    INJECTION_BINDINGS,
    INJECTION_EXCLUDED_MODULES,
    TIER2_L0_BLOCKED_COVERAGE,
    InjectionBinding,
    DriftRisk,
    Family,
    ModelSeamKind,
    NASubreason,
    ObservationPath,
    Tier1Status,
    missing_required_modules,
    tier1_dependency_blocked_reason,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "band"


def _discover_adapter_modules() -> set[str]:
    adapter_dir = _SRC_ROOT / "adapters"
    modules: set[str] = set()
    for p in adapter_dir.iterdir():
        if p.suffix == ".py" and p.name != "__init__.py":
            modules.add(p.stem)
        elif p.is_dir() and (p / "__init__.py").is_file():
            # Package-style adapters (e.g. adapters/codex/) must also be bound
            # or excluded — a package-only adapter cannot skip the gate.
            modules.add(p.name)
    return modules


def _literal_string_sequence(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise AssertionError(
            "tests/e2e/conftest.py adapter_entry params must be a literal "
            "adapter-name sequence"
        )

    adapters: set[str] = set()
    for elt in node.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            raise AssertionError(
                "tests/e2e/conftest.py adapter_entry params must contain only "
                "literal adapter-name strings"
            )
        adapters.add(elt.value)
    return adapters


def _e2e_parametrized_adapters() -> set[str]:
    """Read the shared E2E adapter matrix without importing optional deps."""
    conftest = _REPO_ROOT / "tests" / "e2e" / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"), filename=str(conftest))
    module_constants: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    module_constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_constants[node.target.id] = node.value

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != "adapter_entry":
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "fixture"
                and isinstance(func.value, ast.Name)
                and func.value.id == "pytest"
            ):
                continue
            for keyword in decorator.keywords:
                if keyword.arg != "params":
                    continue
                if isinstance(keyword.value, ast.Name):
                    return _literal_string_sequence(module_constants[keyword.value.id])
                return _literal_string_sequence(keyword.value)
    raise AssertionError("Could not find pytest.fixture(params=...) for adapter_entry")


def _e2e_adapter_factory_names() -> set[str]:
    """Read BASELINE_L0_ADAPTER_FACTORIES keys without importing optional deps."""
    conftest = _REPO_ROOT / "tests" / "e2e" / "adapters" / "conftest.py"
    tree = ast.parse(conftest.read_text(encoding="utf-8"), filename=str(conftest))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            is_factories = any(
                isinstance(target, ast.Name)
                and target.id == "BASELINE_L0_ADAPTER_FACTORIES"
                for target in node.targets
            )
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            is_factories = node.target.id == "BASELINE_L0_ADAPTER_FACTORIES"
            value = node.value
        else:
            continue
        if not is_factories:
            continue
        if not isinstance(value, ast.Dict):
            raise AssertionError("BASELINE_L0_ADAPTER_FACTORIES must be a literal dict")
        names: set[str] = set()
        for key in value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise AssertionError(
                    "BASELINE_L0_ADAPTER_FACTORIES keys must be literal strings"
                )
            names.add(key.value)
        return names
    raise AssertionError(
        "Could not find BASELINE_L0_ADAPTER_FACTORIES in tests/e2e/adapters/conftest.py"
    )


class SeamNotFound(Exception):
    """The seam's attribute path does not exist in the module source."""


def _module_source_path(module_path: str) -> Path:
    """Map a dotted module path under ``band`` to its source file.

    Uses the on-disk layout, NOT import — so seam resolution never requires the
    adapter's optional framework dependency to be installed. This is what keeps
    the rename guarantee honest in a partially-installed CI lane: a missing dep
    can no longer masquerade as a renamed seam (the previous import-based
    resolver skipped on ImportError, which fails OPEN).
    """
    assert module_path.startswith("band."), module_path
    rel = Path(*module_path.split(".")[1:]).with_suffix(".py")
    return _SRC_ROOT / rel


def _assert_seam_defined_in_source(seam: str) -> None:
    """Fail-closed: assert the seam's attribute path is defined in the module's
    source via AST, without importing the module.

    Seam form: ``"band.adapters.x:Class.method"`` or ``"band...:func"``.
    Raises SeamNotFound if any name in the attribute path is absent.
    """
    module_path, _, attr_path = seam.partition(":")
    src_file = _module_source_path(module_path)
    if not src_file.is_file():
        raise SeamNotFound(f"module source not found on disk: {src_file}")

    tree = ast.parse(src_file.read_text(encoding="utf-8"), filename=str(src_file))

    def _names_at(body: list[ast.stmt]) -> dict[str, ast.stmt]:
        out: dict[str, ast.stmt] = {}
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out[node.name] = node
        return out

    scope = _names_at(tree.body)
    parts = attr_path.split(".")
    for i, part in enumerate(parts):
        node = scope.get(part)
        if node is None:
            raise SeamNotFound(
                f"seam {seam!r}: name {part!r} not defined in {src_file.name} "
                f"(path so far: {'.'.join(parts[: i + 1])})"
            )
        if isinstance(node, ast.ClassDef):
            scope = _names_at(node.body)
        else:
            scope = {}  # functions/methods are leaves for our seam paths


# ---------------------------------------------------------------------------
# Coverage: every adapter is bound or excluded; no stale entries.
# ---------------------------------------------------------------------------


class TestInjectionRegistryCoverage:
    def test_every_adapter_module_is_bound_or_excluded(self) -> None:
        source_modules = _discover_adapter_modules()
        bound = {b.adapter for b in INJECTION_BINDINGS}
        covered = bound | INJECTION_EXCLUDED_MODULES
        uncovered = source_modules - covered
        assert not uncovered, (
            f"Adapter modules with no InjectionBinding and not excluded: {uncovered}. "
            f"Add a binding in tests/framework_conformance/injection_registry.py "
            f"(honest seam or N-A with reason), or add to INJECTION_EXCLUDED_MODULES."
        )

    def test_no_stale_bindings(self) -> None:
        source_modules = _discover_adapter_modules()
        stale = {b.adapter for b in INJECTION_BINDINGS} - source_modules
        assert not stale, (
            f"InjectionBindings reference adapter modules that no longer exist: {stale}."
        )

    def test_no_stale_exclusions(self) -> None:
        source_modules = _discover_adapter_modules()
        stale = INJECTION_EXCLUDED_MODULES - source_modules
        assert not stale, (
            f"INJECTION_EXCLUDED_MODULES references modules that no longer exist: {stale}."
        )

    def test_one_binding_per_adapter(self) -> None:
        seen = [b.adapter for b in INJECTION_BINDINGS]
        dupes = {a for a in seen if seen.count(a) > 1}
        assert not dupes, f"Adapters with more than one InjectionBinding: {dupes}."

    def test_bound_and_excluded_are_disjoint(self) -> None:
        bound = {b.adapter for b in INJECTION_BINDINGS}
        overlap = bound & INJECTION_EXCLUDED_MODULES
        assert not overlap, f"Adapters both bound and excluded: {overlap}. Pick one."

    def test_non_bridge_bindings_have_l0_live_factory_or_blocked_artifact(self) -> None:
        bound = {b.adapter for b in INJECTION_BINDINGS}
        live_factories = _e2e_adapter_factory_names()
        blocked = set(TIER2_L0_BLOCKED_COVERAGE)
        missing = bound - live_factories - blocked
        assert not missing, (
            "Non-bridge InjectionBindings with no live L0 E2E factory and no explicit "
            f"blocked artifact coverage: {missing}. Add an adapter factory or a "
            "TIER2_L0_BLOCKED_COVERAGE entry."
        )

    def test_l0_live_blocked_artifact_entries_are_real_and_not_stale(self) -> None:
        bound = {b.adapter for b in INJECTION_BINDINGS}
        live_factories = _e2e_adapter_factory_names()
        for adapter_id, coverage in TIER2_L0_BLOCKED_COVERAGE.items():
            assert adapter_id in bound
            assert coverage.adapter == adapter_id
            assert coverage.status.value == "blocked_artifact_required"
            assert adapter_id not in live_factories, (
                f"{adapter_id}: remove stale blocked coverage now that an E2E factory exists"
            )
            assert coverage.reason.startswith("tier2_blocked:"), coverage.reason
            assert (_REPO_ROOT / coverage.artifact_path).is_file(), (
                f"{adapter_id}: blocked artifact path {coverage.artifact_path!r} "
                "does not exist"
            )


# ---------------------------------------------------------------------------
# Per-binding invariants (parametrized so each adapter fails independently).
# ---------------------------------------------------------------------------

_BINDINGS = list(INJECTION_BINDINGS)
_BINDING_IDS = [b.adapter for b in _BINDINGS]


class TestBindingInvariants:
    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_family_status_consistency(self, binding: InjectionBinding) -> None:
        if binding.family is Family.RUNTIME_OWNED_ROUTING:
            assert binding.tier1_status is Tier1Status.N_A_TIER2, (
                f"{binding.adapter}: RUNTIME_OWNED_ROUTING must be N_A_TIER2"
            )
        else:
            assert binding.is_honest(), (
                f"{binding.adapter}: family {binding.family.value} must carry an honest status"
            )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_honest_binding_has_observation_path(
        self, binding: InjectionBinding
    ) -> None:
        if binding.is_honest():
            assert binding.observation_paths, (
                f"{binding.adapter}: honest binding must declare >=1 observation_path "
                f"so the positive-routing canary asserts on the right recorder bucket"
            )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_honest_binding_seam_is_defined_in_source(
        self, binding: InjectionBinding
    ) -> None:
        """Fail-closed seam check via AST — no import, no optional dep required.

        A renamed/removed seam ALWAYS fails here, even in a CI lane where the
        adapter's framework dep is absent (the previous import-based resolver
        skipped on ImportError, which failed OPEN).
        """
        if not binding.is_honest():
            return
        assert binding.seam, f"{binding.adapter}: honest binding must declare a seam"
        try:
            _assert_seam_defined_in_source(binding.seam)
        except SeamNotFound as exc:
            pytest.fail(
                f"{binding.adapter}: declared seam {binding.seam!r} is not defined in "
                f"source ({exc}). The adapter renamed/removed the seam — update the "
                f"binding and the spike."
            )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_high_drift_version_pin_matches_installed(
        self, binding: InjectionBinding
    ) -> None:
        """HIGH-drift bindings must pin a spec that the INSTALLED framework
        satisfies, so an upstream bump trips the gate before the spike silently
        breaks against a reshaped internal contract.

        Skips only when the framework is genuinely not installed (a different
        failure, surfaced by the spike's importorskip), never on a real mismatch.
        """
        if binding.drift_risk is not DriftRisk.HIGH:
            return
        assert binding.version_pin, (
            f"{binding.adapter}: HIGH drift requires a version_pin"
        )
        req = Requirement(binding.version_pin)
        try:
            installed = importlib.metadata.version(req.name)
        except importlib.metadata.PackageNotFoundError:
            pytest.skip(
                f"{binding.adapter}: pinned framework {req.name!r} not installed; "
                f"version match cannot be checked in this lane"
            )
        assert req.specifier.contains(installed, prereleases=True), (
            f"{binding.adapter}: installed {req.name} {installed} does NOT satisfy the "
            f"binding's version_pin {binding.version_pin!r}. An upstream bump may have "
            f"reshaped the scripted-model contract — re-verify the spike and update the pin."
        )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_honest_binding_spike_exists(self, binding: InjectionBinding) -> None:
        if not binding.is_honest():
            return
        assert binding.spike_test, (
            f"{binding.adapter}: honest binding must link a runnable spike_test"
        )
        spike = _REPO_ROOT / binding.spike_test
        assert spike.is_file(), (
            f"{binding.adapter}: spike_test {binding.spike_test!r} does not exist"
        )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_honest_binding_dependency_contract_is_explicit(
        self, binding: InjectionBinding
    ) -> None:
        if not binding.is_honest():
            return
        if binding.required_modules:
            assert binding.required_extra, (
                f"{binding.adapter}: required_modules must name the install lane"
            )
        else:
            assert binding.adapter == "codex", (
                f"{binding.adapter}: honest bindings with optional framework deps must "
                "declare required_modules; only scripted in-repo replay may omit them"
            )

    def test_request_capture_probe_dependencies_are_declared_on_bindings(self) -> None:
        from tests.framework_conformance.request_capture import REQUEST_CAPTURE_PROBES

        bindings = {binding.adapter: binding for binding in INJECTION_BINDINGS}
        missing: dict[str, str] = {}
        for adapter_id, probe in REQUEST_CAPTURE_PROBES.items():
            if probe.required_module is None:
                continue
            binding = bindings[adapter_id]
            if probe.required_module not in binding.required_modules:
                missing[adapter_id] = probe.required_module

        assert not missing, (
            "Request-capture probes must not own optional dependency truth outside "
            f"InjectionBinding.required_modules: {missing}"
        )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_honest_binding_missing_dependency_reason_is_explicit(
        self, binding
    ) -> None:
        if not binding.is_honest():
            return
        reason = tier1_dependency_blocked_reason(binding)
        missing = missing_required_modules(binding)
        if missing:
            assert reason is not None
            assert reason.startswith("tier1_dependency_blocked:"), reason
            for module in missing:
                assert module in reason
        else:
            assert reason is None

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_model_seam_kind_only_for_injectable(
        self, binding: InjectionBinding
    ) -> None:
        if binding.family is Family.INJECTABLE_MODEL_OBJECT:
            assert isinstance(binding.model_seam_kind, ModelSeamKind), (
                f"{binding.adapter}: INJECTABLE_MODEL_OBJECT must declare model_seam_kind"
            )
        else:
            assert binding.model_seam_kind is None, (
                f"{binding.adapter}: model_seam_kind is only meaningful for "
                f"INJECTABLE_MODEL_OBJECT"
            )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_high_drift_requires_version_pin(self, binding: InjectionBinding) -> None:
        if binding.drift_risk is DriftRisk.HIGH:
            assert binding.version_pin, (
                f"{binding.adapter}: HIGH drift_risk requires a version_pin so an "
                f"upstream framework bump trips the gate before the spike silently breaks"
            )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_na_binding_has_reason_and_tier2(self, binding: InjectionBinding) -> None:
        if binding.tier1_status is not Tier1Status.N_A_TIER2:
            return
        assert isinstance(binding.na_subreason, NASubreason), (
            f"{binding.adapter}: N-A binding must record an na_subreason"
        )
        assert binding.tier2_coverage, (
            f"{binding.adapter}: N-A binding must point at a compensating Tier-2 test "
            f"so it is never a silent gap"
        )
        cov = _REPO_ROOT / binding.tier2_coverage
        assert cov.is_file(), (
            f"{binding.adapter}: tier2_coverage {binding.tier2_coverage!r} does not exist"
        )
        assert cov.is_relative_to(_REPO_ROOT / "tests" / "e2e"), (
            f"{binding.adapter}: N-A tier2_coverage must point at an E2E test, "
            f"not {binding.tier2_coverage!r}"
        )
        if binding.tier2_coverage != "tests/e2e/adapters/test_all_adapters.py":
            content = cov.read_text(encoding="utf-8")
            assert binding.adapter in cov.stem or binding.adapter in content, (
                f"{binding.adapter}: dedicated tier2_coverage "
                f"{binding.tier2_coverage!r} does not mention the adapter; "
                f"use a real adapter-specific E2E file or the shared matrix."
            )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_na_binding_is_reached_by_e2e_matrix(
        self, binding: InjectionBinding
    ) -> None:
        if binding.tier1_status is not Tier1Status.N_A_TIER2:
            return
        assert binding.tier2_coverage
        if binding.tier2_coverage != "tests/e2e/adapters/test_all_adapters.py":
            return

        parametrized = _e2e_parametrized_adapters()
        assert binding.adapter in parametrized, (
            f"{binding.adapter}: tier2_coverage points at the shared adapter E2E "
            f"test, but adapter_entry does not parametrize it. Add {binding.adapter!r} "
            f"to tests/e2e/conftest.py or point tier2_coverage at a dedicated E2E test."
        )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_na_binding_has_no_honest_fields(self, binding: InjectionBinding) -> None:
        if binding.tier1_status is not Tier1Status.N_A_TIER2:
            return
        assert binding.seam is None, (
            f"{binding.adapter}: N-A binding must not declare a seam"
        )
        assert binding.spike_test is None, (
            f"{binding.adapter}: N-A binding must not link a Tier-1 spike"
        )

    @pytest.mark.parametrize("binding", _BINDINGS, ids=_BINDING_IDS)
    def test_observation_paths_are_valid(self, binding: InjectionBinding) -> None:
        for p in binding.observation_paths:
            assert isinstance(p, ObservationPath)
