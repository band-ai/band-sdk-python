"""The matrix: one self-registering builder per LLM-agent adapter (pytest-free).

Each builder lazy-imports its framework and maps the generic ``prompt`` to the
constructor argument that framework uses (prompt / custom_section / system_prompt /
the agent's own instructions). ``supports`` lists the platform capabilities the
adapter advertises for capability-scoped matrices.

This module has no public API: importing it runs the ``@adapter`` decorators, which
populate the registry in ``adapters``. ``adapters`` imports it once (at the bottom of
that module) so the registry is populated before ``specs()`` / ``build_adapter`` query
it. Heavy/optional framework imports live **inside** each builder so importing this
module never pulls in an absent dependency.

To add a framework: add an ``Adapter`` enum member in ``tests.baseline.adapter`` and
a decorated builder here (see ``adapters`` module docstring for the full recipe).
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability
from band.testing import feature_kwargs

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.adapters import (
    Adapter,
    _custom_tool_defs,
    _reject_tools,
    adapter,
)
from tests.e2e.baseline.toolkit.deps import Dep
from tests.e2e.baseline.toolkit.tools import ToolSpec

_LLM_TOOL_LOOP = (Capability.MEMORY, Capability.CONTACTS)
_FILE_CAPABLE = (*_LLM_TOOL_LOOP, Capability.FILES)


@adapter(Adapter.ANTHROPIC, requires=[Dep.ANTHROPIC], supports=_FILE_CAPABLE)
def _build_anthropic(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.anthropic import AnthropicAdapter

    return AnthropicAdapter(
        model=s.llm_models.anthropic_model,
        provider_key=s.llm_credentials.anthropic_api_key or None,
        prompt=prompt,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.CLAUDE_SDK, requires=[Dep.ANTHROPIC], supports=_FILE_CAPABLE)
def _build_claude_sdk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.claude_sdk import ClaudeSDKAdapter

    return ClaudeSDKAdapter(
        model=s.llm_models.anthropic_model,
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(
    Adapter.COPILOT_SDK,
    # Singular BYOK replaces Copilot-hosted inference and needs only the
    # Anthropic provider key; GitHub auth is deliberately disabled below.
    requires=[Dep.ANTHROPIC],
    supports=_FILE_CAPABLE,
)
def _build_copilot_sdk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    # The generic matrix builder is BYOK-on-Anthropic, matching claude_sdk's model;
    # ask_user / base_directory / a shared client are bespoke knobs exercised by
    # tests/e2e/baseline/smoke/adapters/test_copilot_sdk.py, not by this builder.
    from copilot import ProviderConfig

    from band.adapters.copilot_sdk import CopilotSDKAdapter, CopilotSDKAdapterConfig

    return CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            model=s.llm_models.anthropic_model,
            provider=ProviderConfig(
                type="anthropic",
                base_url="https://api.anthropic.com",
                api_key=s.llm_credentials.anthropic_api_key,
            ),
            use_logged_in_user=False,
            custom_section=prompt or "",
        ),
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.LANGGRAPH, requires=[Dep.OPENAI], supports=_FILE_CAPABLE)
def _build_langgraph(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from langchain_openai import ChatOpenAI
    from langgraph.checkpoint.memory import MemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    return LangGraphAdapter(
        llm=ChatOpenAI(
            model=s.llm_models.openai_model,
            api_key=s.llm_credentials.openai_api_key or None,
        ),
        # Deliberately an in-memory checkpointer: it is rebuilt fresh on every
        # cell.run_as, so no LangGraph state survives a reboot in-process. That is
        # what keeps the rehydration scenarios honest for this cell — recall after a
        # reboot can only come from platform /context, not the checkpointer. Swapping
        # in a persistent checkpointer keyed by room_id would silently move langgraph
        # into the codex/opencode "backend session resume" class and invalidate that.
        checkpointer=MemorySaver(),
        custom_section=prompt or "",
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.PYDANTIC_AI, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
def _build_pydantic_ai(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from pydantic_ai import RunContext

    from band.adapters.pydantic_ai import PydanticAIAdapter

    # pydantic-ai takes native callables with a RunContext-first signature.
    native = (
        [t.as_callable(ctx_annotation=RunContext) for t in tools] if tools else None
    )
    return PydanticAIAdapter(
        model=f"openai:{s.llm_models.openai_model}",
        custom_section=prompt,
        additional_tools=native,
        **feature_kwargs(features),
    )


@adapter(Adapter.STRANDS, requires=[Dep.OPENAI], supports=_FILE_CAPABLE)
def _build_strands(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from strands.models.openai import OpenAIModel

    from band.adapters.strands import StrandsAdapter

    # Strands has no provider-prefix string shorthand (a bare string means a
    # Bedrock model id), so the OpenAI provider is constructed explicitly.
    api_key = s.llm_credentials.openai_api_key
    return StrandsAdapter(
        model=OpenAIModel(
            client_args={"api_key": api_key} if api_key else None,
            model_id=s.llm_models.openai_model,
        ),
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.GEMINI, requires=[Dep.GOOGLE], supports=_FILE_CAPABLE)
def _build_gemini(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.gemini import GeminiAdapter

    return GeminiAdapter(
        model=s.llm_models.gemini_model,
        provider_key=s.llm_credentials.google_api_key or None,
        prompt=prompt,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.GOOGLE_ADK, requires=[Dep.GOOGLE], supports=_FILE_CAPABLE)
def _build_google_adk(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.google_adk import GoogleADKAdapter

    # google-adk reads the provider key / Vertex config from the environment.
    return GoogleADKAdapter(
        model=s.llm_models.gemini_model,
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.CREWAI, requires=[Dep.OPENAI, Dep.CREWAI], supports=_LLM_TOOL_LOOP)
def _build_crewai(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.crewai import CrewAIAdapter

    return CrewAIAdapter(
        model=s.llm_models.openai_model,
        role="Test Assistant",
        goal="Help users with simple tasks for testing.",
        backstory="A test agent for E2E validation.",
        custom_section=prompt,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(Adapter.AGNO, requires=[Dep.ANTHROPIC], supports=_FILE_CAPABLE)
def _build_agno(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    # Agno bridges a user-built agent, so steering goes into its instructions.
    # Use the Anthropic model: small models refuse the suite's crafted prompts as
    # injection, so the matrix relies on E2E_ANTHROPIC_MODEL being a capable model.
    from agno.agent import Agent as AgnoAgent
    from agno.models.anthropic import Claude

    from band.adapters.agno import AgnoAdapter

    # agno tools are plain callables on the agent; the band adapter captures them
    # and re-offers them alongside the platform tools each run.
    native = [t.as_callable() for t in tools] if tools else None
    return AgnoAdapter(
        AgnoAgent(
            model=Claude(id=s.llm_models.anthropic_model),
            instructions=prompt,
            tools=native,
        ),
        **feature_kwargs(features),
    )


@adapter(Adapter.CREWAI_FLOW, requires=[Dep.CREWAI], runs_tool_loop=False)
def _build_crewai_flow(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    # CrewAI Flow returns a terminal result rather than running the Band tool loop,
    # so it takes a flow_factory (not a model/prompt) and advertises no platform
    # capabilities. The minimal flow echoes back so the reply path is observable.
    from band.adapters.crewai_flow import CrewAIFlowAdapter

    class _E2EFlow:
        async def kickoff_async(self, inputs: dict[str, Any]) -> dict[str, Any]:
            message = inputs.get("message", {})
            content = message.get("content", "") if isinstance(message, dict) else ""
            return {"decision": "direct_response", "content": content, "mentions": []}

    return CrewAIFlowAdapter(
        flow_factory=_E2EFlow,
        # In the baseline room scenarios crewai_flow is a live participant that must
        # react to peer (agent-authored) messages — e.g. the loop_suppression positive,
        # where a peer's directed probe has to drive a turn. The SDK default is the
        # conservative False (a router ignores agent-initiated turns to avoid A<->B echo
        # loops); opting in here is safe because the runtime already drops an agent's
        # OWN messages before dispatch (execution.py self-filter), so crewai_flow reacts
        # to peers without ever looping on its own output.
        accept_agent_initiated=True,
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


def codex_config_kwargs(s: BaselineSettings, *, prompt: str | None) -> dict[str, Any]:
    """``CodexAdapterConfig`` kwargs shared by the matrix builder and any bespoke
    codex construction (see ``test_codex.py``) that needs a field the matrix
    builder doesn't expose -- so both stay in sync on cwd/model/command instead
    of a bespoke test hand-copying this logic and silently drifting from it.

    Only overrides what's explicitly configured. ``CODEX_MODEL`` is left unset by
    default -- NOT defaulted to the OpenAI chat model: Codex uses its own model
    catalogue (the OpenAI chat model isn't in it), so leaving config.model=None lets the
    adapter discover/select a valid Codex model. ``CODEX_COMMAND`` likewise: an absent
    value spawns the stock `codex` binary. Splits mirror the gates in deps.py.
    """
    config_kwargs: dict[str, Any] = {
        "cwd": s.backends.codex_cwd,
        "custom_section": prompt or "",
    }
    if s.backends.codex_model.strip():
        config_kwargs["model"] = s.backends.codex_model
    if s.backends.codex_command.strip():
        config_kwargs["codex_command"] = tuple(s.backends.codex_command.split())
    return config_kwargs


@adapter(
    Adapter.CODEX,
    requires=[Dep.CODEX_CLI, Dep.CODEX_CWD],
    supports=[Capability.FILES],
    runs_tool_loop=False,
)
def _build_codex(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    return CodexAdapter(
        config=CodexAdapterConfig(**codex_config_kwargs(s, prompt=prompt)),
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


@adapter(
    Adapter.OPENCODE,
    requires=[Dep.OPENCODE_SERVER],
    supports=_FILE_CAPABLE,
    runs_tool_loop=False,
)
def _build_opencode(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

    return OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=s.backends.opencode_base_url,
            provider_id=s.backends.opencode_provider_id,
            model_id=s.backends.opencode_model_id,
            custom_section=prompt or "",
            # Headless rooms have no approver: OpenCode's non-tool asks (e.g.
            # its doom_loop repetition heuristic, which the multi-tool memory
            # smokes trip) would stall to the manual-mode timeout and fail the
            # turn. Mirrors codex, whose baseline runs approvalPolicy="never".
            approval_mode="auto_accept",
        ),
        additional_tools=_custom_tool_defs(tools),
        **feature_kwargs(features),
    )


def copilot_home_dir(work_dir: str) -> str:
    """Create and return the ``copilot-home`` subdirectory of ``work_dir``.

    The one place the subdirectory name and its creation live — the registry
    builder and the bespoke test configs (``test_copilot_acp.py``) all call
    this rather than each re-picking the name and an os.path/pathlib API.
    """
    home = os.path.join(work_dir, "copilot-home")
    os.makedirs(home, exist_ok=True)
    return home


def copilot_acp_env(s: BaselineSettings, copilot_home: str) -> dict[str, str]:
    """Environment for a hermetic, Anthropic-BYOK ``copilot --acp`` spawn.

    BYOK (per ``copilot help providers``): COPILOT_PROVIDER_BASE_URL activates
    it, GitHub authentication is then not required, and a model must be named
    — the same Anthropic key + model the copilot_sdk builder uses, so cells
    validate Copilot CLI on a BYOK model rather than the quota-bound
    Copilot-hosted default. ``copilot_home`` (see ``copilot_home_dir``) should
    be a fresh directory so host-installed extensions and session state
    cannot steer the turn — an installed extension whose description mentions
    Band was observed hijacking a turn (the agent loaded it and never made
    the requested tool call). BYOK auth rides entirely on this env, so hiding
    the home never hides auth.
    """
    return {
        "COPILOT_HOME": copilot_home,
        "COPILOT_PROVIDER_TYPE": "anthropic",
        "COPILOT_PROVIDER_BASE_URL": "https://api.anthropic.com",
        "COPILOT_PROVIDER_API_KEY": s.llm_credentials.anthropic_api_key,
        "COPILOT_MODEL": s.llm_models.anthropic_model,
    }


@adapter(
    Adapter.COPILOT_ACP,
    requires=[Dep.COPILOT_CLI, Dep.ANTHROPIC],
    supports=_FILE_CAPABLE,
    runs_tool_loop=False,
)
def _build_copilot_acp(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig

    # stdio spawn of `copilot --acp` co-located with the SDK, so Band tools reach
    # Copilot over the loopback MCP server (inject_band_tools default True).
    # `supports` and `runs_tool_loop` are separate axes (see AdapterSpec):
    # memory tools are registered and gated on declared capabilities (this
    # adapter is in the capability matrix); contacts stay unconditionally
    # registered like every other caller with no `features=` of its own
    # (see ACPClientAdapter._registered_tools' docstring). The
    # custom-tool round trip (`runs_tool_loop=True`) needs its own live proof —
    # matching codex/opencode/letta, which all delegate tool execution
    # out-of-process and stay `runs_tool_loop=False` until proven. Flip once
    # test_custom_tool_round_trips is observed green.
    #
    # Auth is Anthropic BYOK (copilot_acp_env above), so the gate is the CLI +
    # the Anthropic key — no GitHub token or stored login involved. The spawn
    # is fully hermetic, mirroring codex's disposable CODEX_CWD: a per-cell
    # temp cwd (Copilot discovers project skills and instructions from its
    # working directory — the repo's own .claude/ skills would otherwise leak
    # into the agent under test) and a per-cell COPILOT_HOME.
    # COPILOT_COMMAND overrides the binary + args.
    sandbox = tempfile.mkdtemp(prefix="band-e2e-copilot-acp-")

    config_kwargs: dict[str, Any] = {
        "custom_section": prompt or "",
        "cwd": sandbox,
        "env": copilot_acp_env(s, copilot_home_dir(sandbox)),
    }
    if s.backends.copilot_command.strip():
        config_kwargs["command"] = tuple(s.backends.copilot_command.split())

    built_features = feature_kwargs(features)
    if "emit" in built_features:
        # memory_features()/contacts_features() request Emit.TOOL_CALLS so tool
        # calls surface as tool_call events for the rest of the matrix, but this
        # adapter narrates every tool call unconditionally and declares no
        # SUPPORTED_EMIT (see ACPClientAdapter) -- clamp to what it actually
        # supports so the shared fixture's intent survives without tripping the
        # construction-time unsupported-emit check.
        built_features["emit"] &= CopilotACPAdapter.SUPPORTED_EMIT

    return CopilotACPAdapter(
        config=CopilotACPAdapterConfig(**config_kwargs),
        additional_tools=_custom_tool_defs(tools),
        **built_features,
    )


@adapter(
    Adapter.LETTA,
    requires=[Dep.LETTA],
    supports=[Capability.FILES],
    runs_tool_loop=False,
)
def _build_letta(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.letta import LettaAdapter, LettaAdapterConfig, LettaMCPConfig

    _reject_tools(Adapter.LETTA, tools)

    # An explicit MCP_SERVER_URL selects an external band-mcp (env → default
    # precedence, parity with docker/letta/runner.py). Default is the adapter's
    # self-hosted MCP server: bound only as wide as its advertised host needs —
    # loopback for a natively-run Letta, all interfaces when the dockerized
    # Letta reaches back via host.docker.internal.
    external_url = s.backends.mcp_server_url.strip()
    if external_url:
        mcp = LettaMCPConfig(mode="external", server_url=external_url)
    else:
        advertised = s.backends.letta_mcp_advertised_host
        loopback = advertised in ("127.0.0.1", "localhost")
        mcp = LettaMCPConfig(
            bind_host="127.0.0.1" if loopback else "0.0.0.0",
            advertised_host=advertised,
        )

    return LettaAdapter(
        config=LettaAdapterConfig(
            base_url=s.backends.letta_base_url,
            provider_key=s.backends.letta_api_key or None,
            model=s.backends.letta_model,
            embedding=s.backends.letta_embedding or None,
            mcp=mcp,
            custom_section=prompt or "",
            consolidate_memory_on_cleanup=False,
        ),
        **feature_kwargs(features),
    )
