"""Typed settings for the baseline L0-L4 live (Tier-2) scenarios.

Each level has its own settings class so scenario tests read configuration from
a typed object instead of ``os.environ``. All inherit ``E2ESettings`` (which
loads ``.env.test`` and carries platform creds, provider keys, and adapter
runtime groups), then add the level's live gate, companion agents, and a
``blocked_reason()`` that returns the ``tier2_blocked:`` skip string.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from tests.e2e.conftest import E2ESettings
from tests.e2e.settings_groups import (
    EchoAgentSettings,
    L3CalcAgentSettings,
    L3GreeterAgentSettings,
    L3TestAgentSettings,
)

_DEFAULT_ARTIFACT_DIR = Path("artifacts/e2e-baseline-artifacts")

# Required (non-empty) identity fields per companion-agent role.
_ECHO_REQUIRED = ("id", "api_key", "name", "handle")
_L4_ECHO_REQUIRED = ("id", "name")
_L3_AGENT_REQUIRED = ("id", "api_key", "name", "handle", "description")


class BaselineBaseSettings(E2ESettings):
    """Shared baseline configuration: pricing inputs and artifact location."""

    e2e_baseline_input_usd_per_million_tokens: float | None = None
    e2e_baseline_output_usd_per_million_tokens: float | None = None
    e2e_baseline_pricing_source: str = ""
    e2e_baseline_run_id: str = ""
    e2e_baseline_artifact_dir: str = ""

    @property
    def run_id(self) -> str:
        """Stamped run id, falling back to a UTC timestamp when unset."""
        return self.e2e_baseline_run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    @property
    def artifact_dir(self) -> Path:
        """Directory baseline artifacts are written to."""
        return (
            Path(self.e2e_baseline_artifact_dir)
            if self.e2e_baseline_artifact_dir
            else _DEFAULT_ARTIFACT_DIR
        )


class BaselineL0Settings(BaselineBaseSettings):
    """L0 platform-adaptation live scenario (uses the Echo companion)."""

    e2e_baseline_l0_live: bool = False
    echo: EchoAgentSettings = Field(default_factory=EchoAgentSettings)

    def blocked_reason(self) -> str | None:
        if not self.e2e_baseline_l0_live:
            return "tier2_blocked: E2E_BASELINE_L0_LIVE=true not set for live Echo flow"
        missing = self.echo.missing_env_names(_ECHO_REQUIRED)
        if missing:
            return (
                f"tier2_blocked: missing live Echo configuration {', '.join(missing)}"
            )
        return None


class BaselineL1Settings(BaselineBaseSettings):
    """L1 custom prompt & tools live scenario."""

    e2e_baseline_l1_live: bool = False

    def blocked_reason(self) -> str | None:
        if not self.e2e_baseline_l1_live:
            return "tier2_blocked: E2E_BASELINE_L1_LIVE=true not set for live L1 flow"
        return None


class BaselineL2Settings(BaselineBaseSettings):
    """L2 context-fidelity live scenario."""

    e2e_baseline_l2_live: bool = False

    def blocked_reason(self) -> str | None:
        if not self.e2e_baseline_l2_live:
            return "tier2_blocked: E2E_BASELINE_L2_LIVE=true not set for live L2 flow"
        return None


class BaselineL3Settings(BaselineBaseSettings):
    """L3 multi-participant live scenario (three role agents)."""

    e2e_baseline_l3_live: bool = False
    test_agent: L3TestAgentSettings = Field(default_factory=L3TestAgentSettings)
    calc_agent: L3CalcAgentSettings = Field(default_factory=L3CalcAgentSettings)
    greeter_agent: L3GreeterAgentSettings = Field(
        default_factory=L3GreeterAgentSettings
    )

    def blocked_reason(self) -> str | None:
        if not self.e2e_baseline_l3_live:
            return "tier2_blocked: E2E_BASELINE_L3_LIVE=true not set for live L3 flow"
        missing: list[str] = []
        for agent in (self.test_agent, self.calc_agent, self.greeter_agent):
            missing.extend(agent.missing_env_names(_L3_AGENT_REQUIRED))
        if missing:
            return f"tier2_blocked: missing live L3 configuration {', '.join(missing)}"
        return None


class BaselineL4Settings(BaselineBaseSettings):
    """L4 rehydration live scenario (uses the Echo companion)."""

    e2e_baseline_l4_live: bool = False
    langgraph_restart_smoke: bool = False
    echo: EchoAgentSettings = Field(default_factory=EchoAgentSettings)

    def blocked_reason(self) -> str | None:
        if not self.e2e_baseline_l4_live:
            return "tier2_blocked: E2E_BASELINE_L4_LIVE=true not set for live L4 flow"
        missing = self.echo.missing_env_names(_L4_ECHO_REQUIRED)
        if missing:
            return "tier2_blocked: missing live L4 Echo configuration " + ", ".join(
                missing
            )
        return None

    def langgraph_blocked_reason(self) -> str | None:
        """Extra gate for the LangGraph cold-restart smoke proof."""
        blocked = self.blocked_reason()
        if blocked:
            return blocked
        if not self.langgraph_restart_smoke:
            return (
                "tier2_blocked: LANGGRAPH_RESTART_SMOKE=true not set for supported "
                "LangGraph cold-restart proof"
            )
        return None
