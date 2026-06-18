# Parlant Examples for Band

Examples showing how to use the Band SDK with [Parlant](https://github.com/emcie-co/parlant) - an AI agent framework designed for controlled, guideline-based agent behavior. Band provides the room, identity, @mention routing, audit, participants, and platform tools; Parlant controls one participant's guideline-driven behavior after Band wakes it.

## Why Parlant?

Parlant provides:
- **Behavioral Guidelines**: Define condition/action rules that agents consistently follow
- **Built-in Guardrails**: Prevent hallucination and off-topic responses
- **Explainability**: Understand why agents make specific decisions
- **Production-Ready**: Designed for customer-facing deployments
- **Session Management**: Proper conversation context through the SDK

## Prerequisites

### Install with Parlant support

```bash
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[parlant]"
```

**Or from repository:**
```bash
uv sync --extra dev
```

`dev` is the development extra for this repo. It intentionally avoids CrewAI because Parlant and CrewAI currently require incompatible OpenTelemetry SDK versions; use `dev-crewai` when running CrewAI tests.

---

## Quick Start

The adapter uses the Parlant SDK directly - no separate HTTP server needed:

```python
import parlant.sdk as p
from band import Agent
from band.adapters import ParlantAdapter
async with p.Server(nlp_service=p.NLPServices.openai) as server:
    # Create Parlant agent with example-specific behavior
    parlant_agent = await server.create_agent(
        name="Assistant",
        description="A helpful assistant.",
    )

    await parlant_agent.create_guideline(
        condition="User asks for help",
        action="Acknowledge their request and answer clearly.",
    )

    # Create the Band adapter. It installs the Band platform contract and tools.
    adapter = ParlantAdapter(
        server=server,
        parlant_agent=parlant_agent,
    )

    # Create and run agent
    agent = Agent.create(
        adapter=adapter,
        agent_id="your-agent-id",
        api_key="your-api-key",
    )
    await agent.run()
```

---

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Minimal setup** - OpenAI-backed Parlant SDK agent with a basic assistant persona. |
| `02_with_guidelines.py` | **Behavioral guidelines** - Condition/action rules for example-specific behavior. |
| `03_support_agent.py` | **Customer support** - Support flow with refund, troubleshooting, urgency, and escalation behavior. |
| `04_tom_agent.py` | **Tom character agent** - Character prompt and guideline for staying in character. |
| `05_jerry_agent.py` | **Jerry character agent** - Character prompt and guideline for staying in character. |

---

## Guidelines System

Parlant's guidelines are the key differentiator. They ensure consistent behavior through condition/action pairs:

```python
# Using the Parlant SDK directly
await agent.create_guideline(
    condition="Customer asks about refunds",
    action="Check order status first to see if eligible",
)

await agent.create_guideline(
    condition="User is frustrated",
    action="Acknowledge their frustration before providing solutions",
)
```

---

## Configuration

### 1. Copy configuration files from examples

```bash
# From project root
cp .env.example .env
cp agent_config.yaml.example agent_config.yaml
```

### 2. Set up environment variables in `.env`

```bash
# Band platform URLs (required)
BAND_WS_URL=wss://app.band.ai/api/v1/socket/websocket
BAND_REST_URL=https://app.band.ai

# OpenAI API key (used by Parlant for LLM)
OPENAI_API_KEY=your-openai-key
```

### 3. Add agent credentials to `agent_config.yaml`

1. Create external agents on the Band platform.
2. Generate API keys for those agents.
3. Edit `agent_config.yaml` and fill in the sections used by the examples:

```yaml
parlant_agent:
  agent_id: "your-agent-id-from-platform"
  api_key: "your-api-key-from-platform"

support_agent:
  agent_id: "your-support-agent-id-from-platform"
  api_key: "your-support-agent-api-key-from-platform"

# Required for 04_tom_agent.py and 05_jerry_agent.py
tom_agent:
  agent_id: "your-tom-agent-id-from-platform"
  api_key: "your-tom-agent-api-key-from-platform"

jerry_agent:
  agent_id: "your-jerry-agent-id-from-platform"
  api_key: "your-jerry-agent-api-key-from-platform"
```

> **Note:** Always copy from the example files to ensure correct URLs and formatting. Never hardcode credentials.

---

## Running Examples

**Important:** Run from the project root directory (where `agent_config.yaml` is located):

```bash
# From project root
cd /path/to/band-sdk-python

# Run examples
uv run python examples/parlant/01_basic_agent.py
uv run python examples/parlant/02_with_guidelines.py
uv run python examples/parlant/03_support_agent.py
uv run python examples/parlant/04_tom_agent.py
uv run python examples/parlant/05_jerry_agent.py
```

> **Note:** The config loader looks for `agent_config.yaml` in the current working directory. Running from a subdirectory will cause a `FileNotFoundError`.

---

## Adapter Options

```python
ParlantAdapter(
    # Required: Parlant SDK components
    server=server,           # Parlant Server instance (from p.Server())
    parlant_agent=agent,     # Parlant Agent instance

    # Optional: Custom prompts
    system_prompt=None,      # Full prompt override
    custom_section="...",    # Custom instructions
)
```

`ParlantAdapter` installs the Band platform contract as an always-match Parlant guideline, including the platform tools available for the configured capabilities. Example code should keep Parlant descriptions and guidelines focused on the example-specific behavior, not duplicate Band tool-use instructions. The adapter also supports `additional_tools` with the same `CustomToolDef` tuple format used by other adapters. You can define custom tools with Parlant's native `@p.tool` decorator when you are building Parlant-specific guidelines. Contact and memory tools are capability-gated; memory is supported on enterprise accounts only. Execution reporting is available with `features=AdapterFeatures(emit={Emit.EXECUTION})`.

---

## Use Cases

### Customer Support
Perfect for support agents that need to:
- Follow specific escalation procedures
- Handle sensitive topics appropriately
- Maintain consistent response quality

### Compliance-Critical Applications
Ideal when you need:
- Guaranteed adherence to rules
- Auditable decision-making
- Predictable behavior

### Peer handoff in Band rooms
Works well for:
- Support agents with specific escalation rules
- Specialist agents with domain-specific guidelines
- Agents that identify a useful specialist, add them to the room, and @mention them with context

Band is still the collaboration layer. Parlant does not replace Band's room model or become a central orchestrator; it decides how one participant behaves after that participant is mentioned.

---

## Troubleshooting

### Import errors

```
ImportError: parlant package required for ParlantAdapter
```

Install the Parlant extra:
```bash
uv sync --extra dev
# or, for package consumers
pip install 'band-sdk[parlant]'
```

If Parlant reports provider errors, verify `OPENAI_API_KEY` is set and unset any stale `OPENAI_BASE_URL` or `OPENAI_API_BASE` proxy values unless you intentionally use a compatible OpenAI proxy.
