from __future__ import annotations

from .a_basic_conversation import BasicConversationScenario
from .b_rehydration import RehydrationScenario
from .c_context_isolation import ContextIsolationScenario
from .d_multi_participant import MultiParticipantScenario
from .e_memory_tools import MemoryToolsScenario
from .f_contact_callback import ContactCallbackScenario
from .f_contact_disabled import ContactDisabledScenario
from .f_contact_hub import ContactHubScenario
from .g_execution_emit import ExecutionEmitScenario
from .i_concurrent_rooms import ConcurrentRoomsScenario

CORE_SCENARIOS = [
    BasicConversationScenario,
    RehydrationScenario,
    ContextIsolationScenario,
]

__all__ = [
    "CORE_SCENARIOS",
    "BasicConversationScenario",
    "RehydrationScenario",
    "ContextIsolationScenario",
    "MultiParticipantScenario",
    "MemoryToolsScenario",
    "ContactDisabledScenario",
    "ContactCallbackScenario",
    "ContactHubScenario",
    "ExecutionEmitScenario",
    "ConcurrentRoomsScenario",
]
