"""Exercise Parlant's real tool selection with a controlled model decision."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from parlant.core.agents import Agent, AgentId
from parlant.core.customers import CustomerId
from parlant.core.engines.alpha.optimization_policy import BasicOptimizationPolicy
from parlant.core.engines.alpha.tool_calling.single_tool_batch import (
    NonConsequentialToolBatchSchema,
    SingleToolBatch,
    SingleToolBatchSchema,
)
from parlant.core.engines.alpha.tool_calling.tool_caller import ToolCallContext
from parlant.core.loggers import LogLevel, TracingLogger
from parlant.core.meter import LocalMeter
from parlant.core.nlp.generation_info import GenerationInfo, UsageInfo
from parlant.core.sessions import SessionId
from parlant.core.tools import Tool, ToolId
from parlant.core.tracer import LocalTracer


async def infer_calls(tool: Tool, *, applicable: bool, reason: str) -> list[str]:
    """Both model schemas reject or approve the same populated candidate call."""
    decision = {
        "applicability_rationale": reason,
        "is_applicable": applicable,
        "same_call_is_already_staged": False,
        "relevant_subtleties": reason,
        "argument_evaluations": [
            {
                "parameter_name": "reason",
                "acceptable_source_for_this_argument_according_to_its_tool_definition": "context",
                "evaluate_is_it_provided_by_an_acceptable_source": "yes",
                "evaluate_was_it_already_provided_and_should_it_be_provided_again": "first call",
                "evaluate_is_it_potentially_problematic_to_guess_what_the_value_is_if_it_isnt_provided": "no",
                "is_optional": True,
                "valid_invalid_or_missing": "valid",
                "value_as_string": reason,
            }
        ],
    }
    detailed = SingleToolBatchSchema.model_validate(
        {
            "name": tool.name,
            "subtleties_to_be_aware_of": reason,
            "tool_calls_for_candidate_tool": [decision],
        }
    )
    simple = NonConsequentialToolBatchSchema.model_validate(
        {
            "should_run": applicable,
            "reasoning_tldr": reason,
            "calls": [{"args": {"reason": reason}}],
        }
    )
    info = GenerationInfo("test", "controlled", 0, UsageInfo(0, 0))
    logger = TracingLogger(LocalTracer(), LogLevel.CRITICAL)
    context = ToolCallContext(
        agent=Agent(
            AgentId("agent"),
            "E2E Showcase Agent",
            None,
            datetime.now(UTC),
            1,
            [],
        ),
        session_id=SessionId("session"),
        customer_id=CustomerId("customer"),
        context_variables=[],
        interaction_history=[],
        terms=[],
        ordinary_guideline_matches=[],
        tool_enabled_guideline_matches={},
        journeys=[],
        staged_events=[],
    )
    batch = SingleToolBatch(
        logger=logger,
        meter=LocalMeter(logger),
        optimization_policy=BasicOptimizationPolicy(),
        service_registry=None,  # type: ignore[arg-type] -- inference does not access services
        consequential_schema_generator=AsyncMock(
            generate=AsyncMock(
                return_value=SimpleNamespace(content=detailed, info=info)
            )
        ),
        non_consequential_schema_generator=AsyncMock(
            generate=AsyncMock(return_value=SimpleNamespace(content=simple, info=info))
        ),
        candidate_tool=(ToolId("built-in", tool.name), tool, []),
        context=context,
    )
    return [call.tool_id.tool_name for call in (await batch.process()).tool_calls]
