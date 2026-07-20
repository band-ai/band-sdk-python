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


@adapter(Adapter.ANTHROPIC, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
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
        features=features,
    )


@adapter(Adapter.CLAUDE_SDK, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
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
        features=features,
    )


@adapter(
    Adapter.COPILOT_SDK,
    # Gate on the Anthropic BYOK key only, not a GitHub token: Copilot auth is
    # flexible (env token OR a stored login) and provided out-of-band — a stored
    # login locally, or GITHUB_TOKEN in the CI job env. Same reasoning as copilot_acp.
    requires=[Dep.ANTHROPIC],
    supports=_LLM_TOOL_LOOP,
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
            # A configured token wins; empty -> None so the SDK falls back to the
            # stored `copilot login` (an empty string would not).
            github_token=s.backends.github_token or None,
            custom_section=prompt or "",
        ),
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.LANGGRAPH, requires=[Dep.OPENAI], supports=_LLM_TOOL_LOOP)
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
        features=features,
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
        features=features,
    )


@adapter(Adapter.GEMINI, requires=[Dep.GOOGLE], supports=_LLM_TOOL_LOOP)
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
        features=features,
    )


@adapter(Adapter.GOOGLE_ADK, requires=[Dep.GOOGLE], supports=_LLM_TOOL_LOOP)
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
        features=features,
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
        features=features,
    )


@adapter(Adapter.AGNO, requires=[Dep.ANTHROPIC], supports=_LLM_TOOL_LOOP)
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
        features=features,
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
        features=features,
    )


@adapter(Adapter.CODEX, requires=[Dep.CODEX_CLI, Dep.CODEX_CWD], runs_tool_loop=False)
def _build_codex(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    # Only override what's explicitly configured. CODEX_MODEL is left unset by
    # default -- NOT defaulted to the OpenAI chat model: Codex uses its own model
    # catalogue (the OpenAI chat model isn't in it), so leaving config.model=None lets the
    # adapter discover/select a valid Codex model. CODEX_COMMAND likewise: an absent
    # value spawns the stock `codex` binary. Splits mirror the gates in deps.py.
    config_kwargs: dict[str, Any] = {
        "cwd": s.backends.codex_cwd,
        "custom_section": prompt or "",
    }
    if s.backends.codex_model.strip():
        config_kwargs["model"] = s.backends.codex_model
    if s.backends.codex_command.strip():
        config_kwargs["codex_command"] = tuple(s.backends.codex_command.split())

    return CodexAdapter(
        config=CodexAdapterConfig(**config_kwargs),
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.OPENCODE, requires=[Dep.OPENCODE_SERVER], runs_tool_loop=False)
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
        ),
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


@adapter(Adapter.COPILOT_ACP, requires=[Dep.COPILOT_CLI], runs_tool_loop=False)
def _build_copilot_acp(
    s: BaselineSettings,
    *,
    prompt: str | None,
    features: AdapterFeatures | None,
    tools: list[ToolSpec] | None = None,
) -> SimpleAdapter[Any]:
    from band.adapters.copilot_acp import CopilotACPAdapter, CopilotACPAdapterConfig

    # stdio spawn of `copilot --acp` co-located with the SDK, so Band tools reach
    # Copilot over the loopback MCP server (inject_band_tools default True). Tools are
    # delegated to Copilot over ACP/MCP, so runs_tool_loop=False (matches codex/opencode).
    #
    # Gate on the CLI only — not a token. Copilot accepts several auth methods (env
    # token, stored login in the OS keychain, `gh`, BYOK), and a stored login isn't
    # reliably detectable from settings; so, like codex (which gates on the CLI, not
    # its API key, and logs in out-of-band via setup-codex.sh), auth is provided
    # out-of-band: a stored login locally, or GITHUB_TOKEN in the CI job env (see
    # setup-copilot.sh). We still forward a configured token as a convenience.
    # COPILOT_COMMAND overrides the binary + args.
    # Isolate the spawned CLI from host state, mirroring codex's disposable
    # CODEX_CWD: always a per-cell temp cwd (Copilot discovers project skills
    # and instructions from its working directory — the repo's own .claude/
    # skills would otherwise leak into the agent under test), and, when token
    # auth is configured, a per-cell COPILOT_HOME so host-installed extensions
    # and session state cannot steer the turn — an installed extension whose
    # description mentions Band was observed hijacking the turn (the agent
    # loaded it and never made the requested tool call).
    #
    # COPILOT_HOME isolation is gated on the token because auth is not always
    # outside it: `copilot login` stores its credential in the OS credential
    # store *or falls back to a plain-text file under ~/.copilot/* (per
    # `copilot login --help`). Hiding the home would break the documented
    # ambient-login lane auth for developers on that fallback; a configured
    # token takes precedence over stored credentials, so with one present the
    # home carries no auth the cell needs. CI always configures the token, so
    # CI cells are always hermetic.
    sandbox = tempfile.mkdtemp(prefix="band-e2e-copilot-acp-")
    env: dict[str, str] | None = None
    if s.backends.github_token:
        copilot_home = os.path.join(sandbox, "copilot-home")
        os.makedirs(copilot_home)
        env = {"COPILOT_HOME": copilot_home}

    config_kwargs: dict[str, Any] = {
        "custom_section": prompt or "",
        "github_token": s.backends.github_token or None,
        "cwd": sandbox,
        "env": env,
    }
    if s.backends.copilot_command.strip():
        config_kwargs["command"] = tuple(s.backends.copilot_command.split())

    return CopilotACPAdapter(
        config=CopilotACPAdapterConfig(**config_kwargs),
        additional_tools=_custom_tool_defs(tools),
        features=features,
    )


# Letta advertises no platform capabilities here yet: its tools live on the MCP
# server, and the memory matrix cells assert the band memory-tool loop. The
# self-hosted MCP server *can* serve memory tools (include_memory follows
# Capability.MEMORY), so advertising MEMORY once proven live is a candidate
# follow-up rather than a design limit.
@adapter(Adapter.LETTA, requires=[Dep.LETTA], runs_tool_loop=False)
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
        features=features,
    )
