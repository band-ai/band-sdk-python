"""Shared native OMP smoke helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.timeouts import slow_turn_budget
from tests.e2e.baseline.toolkit.builders import omp_acp_env, omp_agent_home_dir
from tests.e2e.baseline.toolkit.omp_credentials import native_omp_command, omp_model
from tests.paths import REPO_ROOT

if TYPE_CHECKING:
    from band.adapters.omp import OmpAdapter, OmpPermissionRequest

BUDGET = slow_turn_budget(BaselineSettings().e2e_timeout, barriers=1)


def denying_omp_adapter(
    settings: BaselineSettings,
    sandbox: str,
    decision: str | None,
    requests: list[OmpPermissionRequest],
) -> OmpAdapter:
    from band.adapters.omp import (  # noqa: PLC0415 -- the omp extra is absent from other adapter lanes
        OmpAdapter,
        OmpAdapterConfig,
    )
    from tests.e2e.baseline.smoke.samples.omppermission import (  # noqa: PLC0415 -- this policy imports the optional native adapter
        deny_probe_allow_completion,
    )

    async def deny(request: OmpPermissionRequest) -> str | None:
        requests.append(request)
        return await deny_probe_allow_completion(request, probe_decision=decision)

    return OmpAdapter(
        config=OmpAdapterConfig(
            command=(
                *native_omp_command(settings),
                f"--extension={REPO_ROOT / 'tests/e2e/baseline/fixtures/omp-permission-probe.js'}",
            ),
            model=omp_model(settings),
            custom_section="Keep replies short.",
            cwd=sandbox,
            env={
                **omp_acp_env(settings, omp_agent_home_dir(sandbox)),
                "OMP_PERMISSION_PROBE_FILE": str(Path(sandbox) / "permission-result"),
            },
        ),
        resolve_permission=deny,
    )


def permission_probe_sandbox() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="band-e2e-omp-deny-")
