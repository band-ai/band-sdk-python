# Parlant Examples for Thenvoi

Examples showing how to use the Thenvoi SDK with [Parlant](https://github.com/emcie-co/parlant) - an AI agent framework designed for controlled, guideline-based agent behavior.

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
uv sync --extra parlant
```

---

## Quick Start

The adapter uses the Parlant SDK directly - no separate HTTP server needed:

```python
import parlant.sdk as p
from thenvoi import Agent
from thenvoi.adapters import ParlantAdapter

async with p.Server() as server:
    # Create Parlant agent with guidelines
    parlant_agent = await server.create_agent(
        name="Assistant",
        description="A helpful assistant.",
    )

    await parlant_agent.create_guideline(
        condition="User asks for help",
        action="Acknowledge their request and provide detailed assistance",
    )

    # Create Thenvoi adapter
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
| `01_basic_agent.py` | **Minimal setup** - Simple agent with Parlant SDK. |
| `02_with_guidelines.py` | **Behavioral guidelines** - Agent with condition/action rules. |
| `03_support_agent.py` | **Customer support** - Realistic support agent with specialized guidelines. |

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
THENVOI_WS_URL=wss://app.band.ai/api/v1/socket/websocket
THENVOI_REST_URL=https://app.band.ai

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
cd /path/to/thenvoi-sdk-python

# Run examples
uv run python examples/parlant/01_basic_agent.py
uv run python examples/parlant/02_with_guidelines.py
uv run python examples/parlant/03_support_agent.py
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
    system_prompt=None,      # Full system prompt override
    custom_section="...",    # Custom instructions (added to default prompt)
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
pip install 'thenvoi-sdk[parlant]'
```
