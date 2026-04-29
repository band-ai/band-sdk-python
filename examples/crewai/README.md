# CrewAI Examples for Thenvoi

Examples showing how to use the Thenvoi SDK with [CrewAI](https://docs.crewai.com/).

## Why use CrewAI here

CrewAI is a good fit when you want:

- role-based agents with explicit goals and backstories
- multi-agent coordination patterns
- richer prompt shaping than a single flat system prompt
- crew-style room workflows inside Thenvoi

In this repo, the CrewAI adapter lets those agents use Thenvoi rooms and Thenvoi tools.

## What these examples cover

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Minimal setup** - Smallest working CrewAI + Thenvoi example. |
| `02_role_based_agent.py` | **Role definition** - Shows how role, goal, and backstory shape behavior. |
| `03_coordinator_agent.py` | **Coordinator** - Uses Thenvoi tools to discover peers and manage participation in a room. |
| `04_research_crew.py` | **Research crew** - Three agents collaborate in the same room. |
| `05_tom_agent.py` | **Character agent** - Tom the cat with a custom character prompt. |
| `06_jerry_agent.py` | **Character agent** - Jerry the mouse with a custom character prompt. |
| `07_contact_and_memory_agent.py` | **Contacts + memory** - Shows CrewAI contact tools, memory tools, and broadcast contact updates. |
| `08_flow_router.py` | **Flow router (experimental)** - Uses `CrewAIFlowAdapter` for room-native multi-turn orchestration with parallel join and sequential composition. |

## Adapter choice

Two CrewAI adapters ship in the SDK:

- **`CrewAIAdapter`** — the default. Use it for normal CrewAI agent turns:
  one agent in a room, single-hop delegation, bounded one-peer synthesis.
  All seven non-flow examples above use this adapter.
- **`CrewAIFlowAdapter`** — experimental in v1. Use it when you need a
  room router that delegates to multiple peers in parallel, joins their
  replies, composes peer outputs sequentially, or enforces tagged-peer
  routing. The adapter stores all orchestration state in Thenvoi task
  events and reconstructs it from the platform on every turn — peer
  replies arrive as normal room messages and re-enter `on_message` with
  the run's state intact.

`CrewAIFlowAdapter` does not replace `CrewAIAdapter`. Pick the simpler
adapter unless you have an actual parallel-join or sequential-handoff
requirement; the flow adapter writes two task events per visible side
effect (reservation + confirmation) and reads the room's task-event log
on every turn.

For the full decision contract, state model, side-effect behavior, and common
failure modes, see [`README_flow_adapter.md`](README_flow_adapter.md).

## What was validated during `INT-245`

Confirmed live:

- `01_basic_agent.py`
- `02_role_based_agent.py`
- `03_coordinator_agent.py`
- `04_research_crew.py`
- `05_tom_agent.py`
- `06_jerry_agent.py`

Also confirmed live:

- `04_research_crew.py` with separate researcher, writer, and editor processes in one room
- `05_tom_agent.py` and `06_jerry_agent.py` interacting in the same room
- a real CrewAI AMP deployment
- a real AMP re-deploy
- a completed AMP execution with a self-contained deterministic CrewAI smoke project

## Prerequisites

You need:

1. CrewAI dependencies installed
2. model provider credentials in `.env`
3. Thenvoi agent credentials in `agent_config.yaml`
4. commands run from the repo root

## Install

From the repo root:

```bash
uv sync --extra crewai
```

If you are using an OpenAI-compatible model, your `.env` usually needs:

```bash
OPENAI_API_KEY=sk-your-key
```

These examples instantiate the provider during startup. If `OPENAI_API_KEY` is missing, `uv run examples/crewai/01_basic_agent.py` fails before the agent connects to Thenvoi.

## Configuration

### 1. Create local config files

```bash
cp .env.example .env
cp agent_config.yaml.example agent_config.yaml
```

### 2. Add model credentials to `.env`

The examples use OpenAI-compatible model configuration by default.

### 3. Add Thenvoi agent credentials to `agent_config.yaml`

Examples load credentials with `load_agent_config()`, so each script expects a specific config name.

Basic and role-based examples:

```yaml
crewai_agent:
  agent_id: "your-crewai-agent-id"
  api_key: "your-crewai-agent-api-key"
```

Coordinator example:

```yaml
coordinator_agent:
  agent_id: "your-coordinator-agent-id"
  api_key: "your-coordinator-agent-api-key"
```

Research crew:

```yaml
research_agent:
  agent_id: "your-research-agent-id"
  api_key: "your-research-agent-api-key"

writer_agent:
  agent_id: "your-writer-agent-id"
  api_key: "your-writer-agent-api-key"

editor_agent:
  agent_id: "your-editor-agent-id"
  api_key: "your-editor-agent-api-key"
```

Tom and Jerry:

```yaml
tom_agent:
  agent_id: "your-tom-agent-id"
  api_key: "your-tom-agent-api-key"

jerry_agent:
  agent_id: "your-jerry-agent-id"
  api_key: "your-jerry-agent-api-key"
```

Contact, memory, and flow-router examples:

```yaml
crewai_contact_memory_agent:
  agent_id: "your-crewai-contact-memory-agent-id"
  api_key: "your-crewai-contact-memory-agent-api-key"

crewai_flow_router:
  agent_id: "your-crewai-flow-router-agent-id"
  api_key: "your-crewai-flow-router-agent-api-key"
```

## Important runtime note

Run these examples from the repo root.

That matters because:

- `load_agent_config()` expects `agent_config.yaml` in the working directory
- some examples import shared helper modules from the `examples/` tree
- `uv run` uses the local checkout of `thenvoi-sdk`, so branch-local fixes are included

Typical pattern:

```bash
cd /path/to/thenvoi-sdk-python
uv run examples/crewai/01_basic_agent.py
```

## Quick start

If you only want the fastest confidence check, start here:

```bash
uv run examples/crewai/01_basic_agent.py
```

What success looks like:

1. the process starts cleanly
2. the agent connects to Thenvoi
3. you add that agent to a room
4. it replies when you send a message in that room

Once that works, move on to `02` and `03`.

## Example walkthrough

### `01_basic_agent.py`

Use this example if:

- you are setting up CrewAI for the first time
- you want the smallest working integration shape
- you want to verify your Thenvoi and model credentials before debugging anything larger

Run:

```bash
uv run examples/crewai/01_basic_agent.py
```

### `02_role_based_agent.py`

Use this example if:

- you want to see role, goal, and backstory in a simple agent
- you want a better prompt shape without jumping to a full crew

Run:

```bash
uv run examples/crewai/02_role_based_agent.py
```

### `03_coordinator_agent.py`

Use this example if:

- you want one agent to coordinate other peers
- you want to exercise participant-management tools
- you want a room workflow where the agent can invite collaborators

Run:

```bash
uv run examples/crewai/03_coordinator_agent.py
```

In practice, this example is the most useful one for validating that CrewAI and Thenvoi tools are cooperating correctly.

### `04_research_crew.py`

This is the first full multi-agent room workflow.

Run each role in a separate terminal:

```bash
uv run examples/crewai/04_research_crew.py researcher
uv run examples/crewai/04_research_crew.py writer
uv run examples/crewai/04_research_crew.py editor
```

Then:

1. create a Thenvoi room
2. add all three agents
3. send one research request
4. watch the room conversation evolve across the three roles

What success looks like:

- all three processes start
- all three agents join the room
- the room shows multi-agent collaboration rather than one flat reply

### `05_tom_agent.py` and `06_jerry_agent.py`

These are character-style examples built around custom prompts.

Run them in separate terminals:

```bash
uv run examples/crewai/05_tom_agent.py
uv run examples/crewai/06_jerry_agent.py
```

Then:

1. create a Thenvoi room
2. add Tom and Jerry
3. send a prompt to start the interaction
4. watch the responses reflect the two different character prompts

These examples are useful if you want to see how CrewAI behaves when the prompt is personality-heavy rather than task-heavy.

### `07_contact_and_memory_agent.py`

Use this example if:

- you want a CrewAI example that exercises contact tools
- you want memory tools enabled in the adapter
- you want contact changes to show up as room context

Run:

```bash
uv run examples/crewai/07_contact_and_memory_agent.py
```

Suggested prompts:

1. ask it to list contacts or check whether a handle is already connected
2. ask it to send or review a contact request
3. ask it to store a preference as memory
4. ask it later to recall that preference

## CrewAI agent definition

A typical adapter configuration looks like this:

```python
adapter = CrewAIAdapter(
    model="gpt-4o-mini",
    role="Research Assistant",
    goal="Help users find and analyze information",
    backstory="Expert researcher with deep domain knowledge",
    custom_section="Extra instructions",
    enable_execution_reporting=False,
    verbose=False,
)
```

Key ideas:

- `role`
  What the agent is
- `goal`
  What the agent is trying to accomplish
- `backstory`
  The context that shapes how it thinks and responds
- `custom_section`
  Extra instructions when you want to layer on domain or personality guidance

## Multi-agent patterns

### Coordinator pattern

Use a single coordinator when you want one agent to:

1. understand the request
2. find the right peers
3. invite them into the room
4. direct the work
5. summarize the result

### Specialist pattern

Use multiple focused agents when you want:

- clearer division of work
- easier prompt tuning per role
- room conversations that show the work happening in steps

### Character pattern

Use custom prompts when you care more about persona, voice, or roleplay-style behavior than formal task decomposition.

## Model support

The adapter uses an OpenAI-compatible model interface.

That includes:

- `gpt-4o`
- `gpt-4o-mini`
- `gpt-4-turbo`
- other OpenAI-compatible models

## Common problems

### `agent_config.yaml` is not found

Run the example from the repo root.

### `OPENAI_API_KEY is required`

This is the current first-run failure mode if model credentials are missing. Add a valid provider key to `.env` before running any CrewAI example.

### The agent starts but never gives a useful reply

Check:

- your model credentials are present
- the provider key is valid
- the agent was added to a Thenvoi room

### Coordinator example does not add anyone

Make sure the workspace actually has peers available for lookup and invitation.

### Character examples feel flat

Those examples depend heavily on model quality and prompt interpretation. They are better for behavior demos than for strict deterministic smoke tests.

## Which example should I use?

- Use `01` for first-run validation
- Use `02` for role/goal/backstory behavior
- Use `03` for orchestration and Thenvoi tool usage
- Use `04` for full multi-agent room collaboration
- Use `05` and `06` for personality-driven agent behavior

## Learn more

- [CrewAI Documentation](https://docs.crewai.com/)
- [CrewAI GitHub](https://github.com/crewAIInc/crewAI)
- [Thenvoi SDK Documentation](https://github.com/thenvoi/thenvoi-sdk-python)
