"""Opaque custom tools for tool-observation smokes.

Defined once and shared across the tool-capture smokes. They are deliberately
*opaque*: each returns information the model cannot produce on its own (a secret
access code, a fictional forecast), so to satisfy the request the agent has no
choice but to call the tool. Tools that merely compute what the model already
knows (arithmetic, echoing text) get skipped despite any "you must call it"
instruction, which makes the tool-fired assertion unreliable.

Why opacity rather than just a strong prompt: the adapter never *forces* tool
use (it passes no ``tool_choice`` to the model), so whether a given tool fires is
the model's free choice. Making the tool the only way to answer is therefore the
only way to assert "tool X fired" deterministically without a production change.

Wire any of these into a test with ``@with_agents(Adapter.X, tools=[LOOKUP_TOOL],
prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING)`` (or ``build_adapter(...)`` for a
bespoke per-agent build); ``EXECUTION_REPORTING`` is what surfaces tool calls.
"""

from __future__ import annotations

from pydantic import BaseModel

from band.core.types import AdapterFeatures, Emit
from band.runtime.custom_tools import get_custom_tool_name
from tests.e2e.baseline.toolkit.tools import ToolSpec

# Secret values the model cannot guess; it must call the tool to obtain them.
ACCESS_CODES = {"alpha": "ZX417", "beta": "QM920", "gamma": "TR365"}
FORECASTS = {"zorath": "ammonia rain at 400 K", "qyx": "triple sunrise, then calm"}


class LookupInput(BaseModel):
    """Look up the secret access code for a project key (cannot be guessed)."""

    key: str
    note: str | None = None


def _lookup(args: LookupInput) -> str:
    return ACCESS_CODES.get(args.key.lower(), "NO-SUCH-CODE")


class WeatherInput(BaseModel):
    """Get the (fictional) weather forecast for a place (cannot be guessed)."""

    place: str


def _weather(args: WeatherInput) -> str:
    return FORECASTS.get(args.place.lower(), "clear skies")


# Tool names derived from the models so prompts and assertions can't drift.
LOOKUP = get_custom_tool_name(LookupInput)
WEATHER = get_custom_tool_name(WeatherInput)

LOOKUP_TOOL = ToolSpec(LookupInput, _lookup)
WEATHER_TOOL = ToolSpec(WeatherInput, _weather)

LOOKUP_PROMPT = (
    f"You have a tool `{LOOKUP}` that returns the secret access code for a key. "
    f"You do NOT know these codes yourself, so you MUST call `{LOOKUP}` to get "
    "one. Then report the code in one short sentence using band_send_message."
)
WEATHER_PROMPT = (
    f"You have a tool `{WEATHER}` that returns the forecast for a place. You do "
    f"NOT know these forecasts, so you MUST call `{WEATHER}`. Then report it in "
    "one short sentence using band_send_message."
)
LOOKUP_AND_WEATHER_PROMPT = (
    f"You have two tools: `{LOOKUP}` (secret access code for a key) and "
    f"`{WEATHER}` (forecast for a place). You do not know these values yourself, "
    "so you MUST call the matching tool for each request, then report both "
    "results using band_send_message."
)


# Shape for @with_agents / build_adapter: surface each tool call as a ``tool_call``
# event so ``capture.tool_calls`` can read it. Spread it: ``@with_agents(
# Adapter.ANTHROPIC, tools=[LOOKUP_TOOL], prompt=LOOKUP_PROMPT, **EXECUTION_REPORTING)``.
EXECUTION_REPORTING = {"features": AdapterFeatures(emit={Emit.EXECUTION})}
