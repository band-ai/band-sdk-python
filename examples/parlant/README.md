# Parlant Examples for Band

Examples showing how to use the Band SDK with [Parlant](https://github.com/emcie-co/parlant) - an AI agent framework designed for controlled, guideline-based agent behavior.

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
uv add "git+https://github.com/band-ai/band-sdk-python.git[parlant]"
```

**Or from repository:**
```bash
uv sync --extra parlant
```

---

## Quick Start

The adapter owns the Parlant server: it reserves free ports, boots `p.Server`
when the Band agent starts, and tears it down on stop. No server or port
wiring in your code:

```python notest
import parlant.sdk as p
from band import Agent
from band.adapters import ParlantAdapter

adapter = ParlantAdapter(
    name="Assistant",
    description="A helpful assistant.",
    nlp_service=p.NLPServices.openai,  # reads OPENAI_API_KEY
)

adapter.add_guideline(
    condition="User asks for help",
    action="Acknowledge their request and provide detailed assistance",
)

agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
)
await agent.run()
```

Prefer an explicit lifecycle? `Agent` is an async context manager — same
behavior, resources boot on enter, graceful teardown on exit:

```python notest
async with Agent.create(adapter=adapter, agent_id="...", api_key="...") as agent:
    await agent.run_forever()
```

---

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Minimal setup** - Simple agent with Parlant SDK. |
| `02_with_guidelines.py` | **Behavioral guidelines** - Agent with condition/action rules. |
| `03_support_agent.py` | **Customer support** - Behavior-only guidelines (`tools=[]`). |
| `04_tom_agent.py` | **Character agent** - Tom, runs side by side with Jerry. |
| `05_jerry_agent.py` | **Character agent** - Jerry, runs side by side with Tom. |

---

## Guidelines System

Parlant's guidelines are the key differentiator. They ensure consistent behavior
through condition/action pairs. Declare them on the adapter before the agent
starts — they are created on the live Parlant agent at startup, with the Band
platform tools (send message, add participant, ...) attached by default:

```python notest
adapter.add_guideline(
    condition="Customer asks about refunds",
    action="Check order status first to see if eligible",
)

# Behavior-only guideline: opt out of the default Band tools
adapter.add_guideline(
    condition="User is frustrated",
    action="Acknowledge their frustration before providing solutions",
    tools=[],
)
```

Extra keyword arguments are forwarded verbatim to Parlant's
`create_guideline`, so Parlant's own documentation applies.

Need the full native API (journeys, guideline dependencies, canned
responses)? Pass a `configure=` callback — it runs at startup with the live
`(server, parlant_agent)`:

```python notest
async def configure(server: p.Server, parlant_agent: p.Agent) -> None:
    await parlant_agent.create_journey(...)

adapter = ParlantAdapter(name="Assistant", description="...", configure=configure)
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

1. Create a remote agent on the [Band Platform](https://app.band.ai)
2. Generate an API key for the agent
3. Edit `agent_config.yaml` and fill in the Parlant agent section:

```yaml
parlant_agent:
  agent_id: "your-agent-id-from-platform"
  api_key: "your-api-key-from-platform"
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
```

> **Note:** The config loader looks for `agent_config.yaml` in the current working directory. Running from a subdirectory will cause a `FileNotFoundError`.

### Running two agents locally (Tom and Jerry)

Parlant's server ports default to fixed numbers (`8800` API, `8818` tool
service), so two stock Parlant servers on one machine collide. The adapter
handles this: each agent's server boots on freshly reserved free ports, so
side-by-side agents just work:

```bash
# terminal 1
uv run python examples/parlant/04_tom_agent.py

# terminal 2 (while Tom is still running)
uv run python examples/parlant/05_jerry_agent.py
```

Each process logs the pair it reserved, so a refused connection can be traced
back to a known port:

```
Parlant server ports: api=54231, tool_service=54232
```

---

## Adapter Options

```python notest
ParlantAdapter(
    # Parlant agent identity (defaults to the Band agent's name/description)
    name="Assistant",
    description="A helpful assistant.",

    # Adapter-owned server configuration
    nlp_service=p.NLPServices.openai,  # Parlant's default (Emcie) if omitted
    server_options={...},              # extra p.Server(...) kwargs, verbatim

    # Escape hatches
    configure=my_callback,             # async (server, parlant_agent) at startup
    server=my_server,                  # bring your own running p.Server (borrowed)
    parlant_agent=my_agent,            # bring your own p.Agent (requires server=)

    # Optional: Custom prompts (adapter-created agent only,
    # not combinable with parlant_agent=)
    system_prompt=None,                # Full override of the created agent's description
    custom_section="...",              # Extra instructions appended to the description
)
```

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

### Multi-Agent Orchestration
Works well for:
- Coordinator agents with specific handoff rules
- Specialist agents with domain-specific guidelines
- Agents that need to collaborate consistently

---

## Troubleshooting

### Import errors

```
ImportError: parlant package required for ParlantAdapter
```

Install the Parlant extra:
```bash
uv sync --extra parlant
# or
pip install 'band-sdk[parlant]'
```
