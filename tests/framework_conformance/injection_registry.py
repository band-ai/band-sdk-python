"""The Tier-1 ``InjectionBinding`` registry (INT-826 / INT-827).

This is the single source of truth for *how every adapter participates in Tier-1
conformance*: which family it belongs to, the declared seam its translator
installs at, where dispatch is observed, and — for the adapters that have no honest
in-isolation seam — the recorded N-A reason plus the Tier-2/E2E test that
compensates.

The companion ``test_injection_binding_drift.py`` fail-closes on this registry: a
new adapter cannot be added without declaring a binding, an honest binding cannot
point at a missing seam or a missing spike, and an N-A binding cannot exist without
a resolvable ``tier2_coverage`` test. See
``docs/baseline-conformance/tier1-injection-contract.md`` §5.6.

This module is intentionally dependency-light (no adapter imports) so the drift
gate can introspect it without pulling optional framework deps into the process.
Seams are declared as ``"module:attribute"`` strings and resolved lazily by the
gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Family(str, Enum):
    """Seam-kind: where the faked decision installs + whether real dispatch is
    reachable in-process. The taxonomy axis (NOT status, NOT drift)."""

    INJECTABLE_MODEL_OBJECT = "injectable_model_object"
    INTERNAL_CLIENT_CALL = "internal_client_call"
    SCRIPTED_PROTOCOL_CLIENT = "scripted_protocol_client"
    RUNTIME_OWNED_ROUTING = "runtime_owned_routing"  # N-A → Tier-2/E2E


class Tier1Status(str, Enum):
    HONEST_TODAY = "honest_today"
    HONEST_VIA_DECLARED_INTERNAL_SEAM = "honest_via_declared_internal_seam"
    N_A_TIER2 = "n_a_tier2"


class ModelSeamKind(str, Enum):
    """INJECTABLE_MODEL_OBJECT only: stability class of the scripted surface."""

    PUBLIC_TEST_MODEL = (
        "public_test_model"  # framework-sanctioned (BaseChatModel, FunctionModel)
    )
    INTERNAL_MODEL_SUBCLASS = (
        "internal_model_subclass"  # framework-internal (ADK BaseLlm)
    )


class DriftRisk(str, Enum):
    LOW = "low"
    HIGH = "high"


class ObservationPath(str, Enum):
    """Where platform-tool dispatch is observed on the shared recorder."""

    EXECUTE_TOOL_CALL = "execute_tool_call"  # recorded on FakeAgentTools.tool_calls
    TYPED_METHODS = (
        "typed_methods"  # recorded on messages_sent / participants_added / ...
    )


class NASubreason(str, Enum):
    """RUNTIME_OWNED_ROUTING only: the specific reason no honest seam exists."""

    IN_PROCESS_PRIVATE_PARSER = "in_process_private_parser"  # CrewAI
    NO_MODEL_DECISION_AT_ROUTING_BOUNDARY = (
        "no_model_decision_at_routing_boundary"  # CrewAI-Flow
    )
    IN_PROCESS_FRAMEWORK_RUNTIME = "in_process_framework_runtime"  # Parlant
    OUT_OF_PROCESS_SUBPROCESS_DECISION = (
        "out_of_process_subprocess_decision"  # Claude SDK
    )
    OUT_OF_PROCESS_SERVER_DECISION = "out_of_process_server_decision"  # OpenCode
    OUT_OF_PROCESS_REMOTE_DECISION = "out_of_process_remote_decision"  # Letta


@dataclass(frozen=True)
class InjectionBinding:
    """How one adapter participates in Tier-1 conformance.

    ``adapter`` is the adapter module base name (matches ``src/thenvoi/adapters/<adapter>.py``).
    ``seam`` is a ``"module:attribute"`` string the drift gate resolves to a real
    callable; for INJECTABLE_MODEL_OBJECT it is the construction/override point,
    for INTERNAL_CLIENT_CALL the ``_call_*`` method, for SCRIPTED_PROTOCOL_CLIENT
    the client-factory consumer.
    """

    adapter: str
    family: Family
    tier1_status: Tier1Status
    drift_risk: DriftRisk
    observation_paths: frozenset[ObservationPath] = field(default_factory=frozenset)

    # Honest families (HONEST_TODAY / HONEST_VIA_DECLARED_INTERNAL_SEAM):
    seam: str | None = None
    model_seam_kind: ModelSeamKind | None = None
    spike_test: str | None = None  # repo-relative path to the runnable proof
    version_pin: str | None = None  # required when drift_risk == HIGH

    # RUNTIME_OWNED_ROUTING (N_A_TIER2):
    na_subreason: NASubreason | None = None
    tier2_coverage: str | None = (
        None  # repo-relative path to the compensating E2E/integration test
    )

    def is_honest(self) -> bool:
        return self.tier1_status in (
            Tier1Status.HONEST_TODAY,
            Tier1Status.HONEST_VIA_DECLARED_INTERNAL_SEAM,
        )


_SPIKE_DIR = "tests/framework_conformance"

# Adapter modules intentionally outside the Tier-1 taxonomy: protocol bridges with
# a non-standard lifecycle (no on_message model→tool path). Same set the contract
# scopes out in §5.6.
INJECTION_EXCLUDED_MODULES: frozenset[str] = frozenset({"a2a", "a2a_gateway", "acp"})


INJECTION_BINDINGS: tuple[InjectionBinding, ...] = (
    # ---- INJECTABLE_MODEL_OBJECT ------------------------------------------
    InjectionBinding(
        adapter="langgraph",
        family=Family.INJECTABLE_MODEL_OBJECT,
        tier1_status=Tier1Status.HONEST_TODAY,
        drift_risk=DriftRisk.LOW,
        observation_paths=frozenset({ObservationPath.EXECUTE_TOOL_CALL}),
        seam="thenvoi.adapters.langgraph:LangGraphAdapter.__init__",  # the `llm=` ctor arg
        model_seam_kind=ModelSeamKind.PUBLIC_TEST_MODEL,
        spike_test=f"{_SPIKE_DIR}/test_injection_proof_spike.py",
    ),
    InjectionBinding(
        adapter="pydantic_ai",
        family=Family.INJECTABLE_MODEL_OBJECT,
        tier1_status=Tier1Status.HONEST_TODAY,
        drift_risk=DriftRisk.LOW,
        # PydanticAI platform tools call typed AgentToolsProtocol methods directly
        # (ctx.deps.send_message), NOT execute_tool_call. See pydantic_ai.py:168.
        observation_paths=frozenset({ObservationPath.TYPED_METHODS}),
        seam="thenvoi.adapters.pydantic_ai:PydanticAIAdapter._create_agent",
        model_seam_kind=ModelSeamKind.PUBLIC_TEST_MODEL,
        spike_test=f"{_SPIKE_DIR}/test_pydantic_ai_injection_spike.py",
    ),
    InjectionBinding(
        adapter="google_adk",
        family=Family.INJECTABLE_MODEL_OBJECT,
        tier1_status=Tier1Status.HONEST_VIA_DECLARED_INTERNAL_SEAM,
        drift_risk=DriftRisk.HIGH,
        observation_paths=frozenset({ObservationPath.EXECUTE_TOOL_CALL}),
        seam="thenvoi.adapters.google_adk:GoogleADKAdapter._create_runner",
        model_seam_kind=ModelSeamKind.INTERNAL_MODEL_SUBCLASS,
        spike_test=f"{_SPIKE_DIR}/test_google_adk_injection_spike.py",
        version_pin="google-adk>=1.0,<2",
    ),
    # ---- INTERNAL_CLIENT_CALL ---------------------------------------------
    InjectionBinding(
        adapter="anthropic",
        family=Family.INTERNAL_CLIENT_CALL,
        tier1_status=Tier1Status.HONEST_TODAY,
        drift_risk=DriftRisk.LOW,
        observation_paths=frozenset({ObservationPath.EXECUTE_TOOL_CALL}),
        seam="thenvoi.adapters.anthropic:AnthropicAdapter._call_anthropic",
        spike_test=f"{_SPIKE_DIR}/test_injection_proof_spike.py",
    ),
    InjectionBinding(
        adapter="gemini",
        family=Family.INTERNAL_CLIENT_CALL,
        tier1_status=Tier1Status.HONEST_TODAY,
        drift_risk=DriftRisk.LOW,
        observation_paths=frozenset({ObservationPath.EXECUTE_TOOL_CALL}),
        seam="thenvoi.adapters.gemini:GeminiAdapter._call_gemini",
        spike_test=f"{_SPIKE_DIR}/test_gemini_injection_spike.py",
    ),
    # ---- SCRIPTED_PROTOCOL_CLIENT -----------------------------------------
    InjectionBinding(
        adapter="codex",
        family=Family.SCRIPTED_PROTOCOL_CLIENT,
        tier1_status=Tier1Status.HONEST_TODAY,
        drift_risk=DriftRisk.LOW,
        observation_paths=frozenset({ObservationPath.EXECUTE_TOOL_CALL}),
        seam="thenvoi.adapters.codex:CodexAdapter._build_client",  # consumes client_factory
        spike_test=f"{_SPIKE_DIR}/test_codex_injection_spike.py",
    ),
    # ---- RUNTIME_OWNED_ROUTING (N-A → Tier-2/E2E) --------------------------
    InjectionBinding(
        adapter="crewai",
        family=Family.RUNTIME_OWNED_ROUTING,
        tier1_status=Tier1Status.N_A_TIER2,
        drift_risk=DriftRisk.LOW,
        na_subreason=NASubreason.IN_PROCESS_PRIVATE_PARSER,
        tier2_coverage="tests/e2e/adapters/test_all_adapters.py",
    ),
    InjectionBinding(
        adapter="crewai_flow",
        family=Family.RUNTIME_OWNED_ROUTING,
        tier1_status=Tier1Status.N_A_TIER2,
        drift_risk=DriftRisk.LOW,
        na_subreason=NASubreason.NO_MODEL_DECISION_AT_ROUTING_BOUNDARY,
        tier2_coverage="tests/adapters/test_crewai_flow_adapter.py",
    ),
    InjectionBinding(
        adapter="parlant",
        family=Family.RUNTIME_OWNED_ROUTING,
        tier1_status=Tier1Status.N_A_TIER2,
        drift_risk=DriftRisk.LOW,
        na_subreason=NASubreason.IN_PROCESS_FRAMEWORK_RUNTIME,
        tier2_coverage="tests/e2e/adapters/test_parlant.py",
    ),
    InjectionBinding(
        adapter="claude_sdk",
        family=Family.RUNTIME_OWNED_ROUTING,
        tier1_status=Tier1Status.N_A_TIER2,
        drift_risk=DriftRisk.LOW,
        na_subreason=NASubreason.OUT_OF_PROCESS_SUBPROCESS_DECISION,
        tier2_coverage="tests/e2e/adapters/test_all_adapters.py",
    ),
    InjectionBinding(
        adapter="opencode",
        family=Family.RUNTIME_OWNED_ROUTING,
        tier1_status=Tier1Status.N_A_TIER2,
        drift_risk=DriftRisk.LOW,
        na_subreason=NASubreason.OUT_OF_PROCESS_SERVER_DECISION,
        tier2_coverage="tests/adapters/test_opencode_adapter.py",
    ),
    InjectionBinding(
        adapter="letta",
        family=Family.RUNTIME_OWNED_ROUTING,
        tier1_status=Tier1Status.N_A_TIER2,
        drift_risk=DriftRisk.LOW,
        na_subreason=NASubreason.OUT_OF_PROCESS_REMOTE_DECISION,
        tier2_coverage="tests/adapters/test_letta_adapter.py",
    ),
)


def bindings_by_adapter() -> dict[str, InjectionBinding]:
    return {b.adapter: b for b in INJECTION_BINDINGS}
