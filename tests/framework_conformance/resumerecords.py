"""The registry of resume-record probes and the helpers that read their turns.

The rule they check lives in ``band.runtime.sessionresume``. Each probe (see
``resumeprobes.probe``) drives one seeding turn through the adapter's real
``on_event``.

To add an adapter: write its probe in ``resumeprobes/<name>.py`` and register
it in ``RESUME_PROBES`` under its class name; a subclass is covered by its
registered parent's probe. ``test_every_adapter_using_the_rule_has_a_probe``
fails when an adapter posts a ``ResumeRecord`` without one.
"""

from __future__ import annotations

import sys

import tests.framework_conformance.resumeprobes.copilotsdk as copilot_resume_probe
from band.converters.acp_client import ACP_CLIENT_RESUME_RECORD
from band.converters.claude_sdk import CLAUDE_SDK_RESUME_RECORD
from band.converters.copilot_sdk import COPILOT_RESUME_RECORD
from band.converters.omp import OMP_RESUME_RECORD
from band.converters.opencode import OPENCODE_RESUME_RECORD
from band.core.simple_adapter import SimpleAdapter
from band.core.types import MessageType
from band.runtime.sessionresume import ResumeRecord
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.resumeprobes import acp, claudesdk, omp
from tests.framework_conformance.resumeprobes.opencode import (
    seed_failure as opencode_seed_failure,
)
from tests.framework_conformance.resumeprobes.opencode import seed_opencode
from tests.framework_conformance.resumeprobes.probe import ResumeProbe

RESUME_PROBES: dict[str, ResumeProbe] = {
    "ACPClientAdapter": ResumeProbe(
        ACP_CLIENT_RESUME_RECORD, acp.seed_acp, acp.seed_failure
    ),
    "OmpACPAdapter": ResumeProbe(
        ACP_CLIENT_RESUME_RECORD, acp.seed_omp_acp, acp.seed_failure
    ),
    "ClaudeSDKAdapter": ResumeProbe(
        CLAUDE_SDK_RESUME_RECORD, claudesdk.seed_claude_sdk, claudesdk.seed_failure
    ),
    "CopilotSDKAdapter": ResumeProbe(
        COPILOT_RESUME_RECORD,
        copilot_resume_probe.seed_copilot_sdk,
        copilot_resume_probe.seed_failure,
    ),
    "OmpAdapter": ResumeProbe(OMP_RESUME_RECORD, omp.seed_omp, omp.seed_failure),
    "OpencodeAdapter": ResumeProbe(
        OPENCODE_RESUME_RECORD, seed_opencode, opencode_seed_failure
    ),
}


def resume_records(
    record: ResumeRecord, tools: FakeAgentTools
) -> list[dict[str, object]]:
    """The task events ``tools`` posted as ``record``."""
    return [
        event
        for event in tools.events_sent
        if event["message_type"] == MessageType.TASK
        and record.room_key in event["metadata"]
    ]


def uses_session_resume(cls: type[SimpleAdapter]) -> bool:
    """Whether a module defining part of ``cls`` holds a ``ResumeRecord``."""
    return any(
        isinstance(value, ResumeRecord)
        for klass in cls.__mro__
        for value in vars(sys.modules[klass.__module__]).values()
    )


def probed(cls: type[SimpleAdapter], adapters: dict[str, type[SimpleAdapter]]) -> bool:
    """Whether ``cls`` or a parent of it has a probe."""
    return any(
        name in adapters and issubclass(cls, adapters[name]) for name in RESUME_PROBES
    )
