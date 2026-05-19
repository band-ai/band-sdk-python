# Mixed Multi-Agent Example

This example puts multiple integration styles in one shared Thenvoi room:

- 2 native CrewAI agents running as normal Thenvoi agents
- 2 external A2A services running as local HTTP agents
- 1 bridge process that connects both external A2A services to Thenvoi so they act like room participants

The result is a room where native agents and bridged external agents can all react to the same engineering request.

## Scenario

Use this setup as a release-readiness room for a real SDK or integration change.

The user asks the room to sanity-check a change before ship. The agents split the work like this:

- `01_strategy_coordinator.py`
  Keeps the room moving, asks the other agents for the right inputs, and makes sure the request turns into a usable engineering handoff.
- `02_draft_writer.py`
  Waits for the room's findings, then writes the final engineering note.
- `03_fact_checker_a2a.py`
  Runs outside Thenvoi as a local A2A service. It acts like an API contract checker and returns concrete implementation facts.
- `04_risk_reviewer_a2a.py`
  Runs outside Thenvoi as a local A2A service. It returns compatibility, rollout, rollback, and observability risks.
- `05_a2a_bridge.py`
  Starts two Thenvoi bridge agents in one process. One forwards room messages to the contract checker A2A service. The other forwards room messages to the risk reviewer A2A service.

## Why the bridge matters

Without the bridge, the A2A services are just local HTTP agents. You can call them directly over A2A, but they are not in your Thenvoi room and they do not see room traffic.

With the bridge running:

- each external A2A service gets a Thenvoi-facing bridge agent
- those bridge agents connect to the platform WebSocket
- room messages are forwarded to the external A2A services
- A2A replies come back into the room as normal agent messages

That is what makes the external services bidirectional participants instead of isolated A2A endpoints.

## Files

| File | Role |
|------|------|
| `01_strategy_coordinator.py` | CrewAI coordinator |
| `02_draft_writer.py` | CrewAI engineering handoff writer |
| `03_fact_checker_a2a.py` | External A2A contract checker |
| `04_risk_reviewer_a2a.py` | External A2A risk reviewer |
| `05_a2a_bridge.py` | Dual bridge launcher for both A2A services |

## Prerequisites

You need:

- `OPENAI_API_KEY` for the CrewAI agents
- optional: `OPENAI_MODEL` if you do not want the default `gpt-5.4-mini`
- four Thenvoi agent credentials in `examples/mixed/agents.yaml`

The mixed examples default to the hosted Thenvoi URLs:

- `THENVOI_WS_URL=wss://app.thenvoi.com/api/v1/socket/websocket`
- `THENVOI_REST_URL=https://app.thenvoi.com`

Only set those vars if you want to point the example at another environment.

Copy the example file first:

```bash
cp examples/mixed/agents.yaml.example examples/mixed/agents.yaml
```

Then fill in these entries:

```yaml
mixed_strategy_agent:
  agent_id: "your-strategy-agent-id"
  api_key: "your-strategy-agent-api-key"

mixed_writer_agent:
  agent_id: "your-writer-agent-id"
  api_key: "your-writer-agent-api-key"

mixed_fact_bridge_agent:
  agent_id: "your-fact-bridge-agent-id"
  api_key: "your-fact-bridge-agent-api-key"

mixed_risk_bridge_agent:
  agent_id: "your-risk-bridge-agent-id"
  api_key: "your-risk-bridge-agent-api-key"
```

The mixed scripts load this file directly.
Internally they use the same `load_agent_config(...)` helper as the other examples,
just with `examples/mixed/agents.yaml` as the explicit config path.
The YAML structure is the same keyed format used by the repo root `agent_config.yaml`.

If you are creating these agents specifically for this example, the clearest platform names are:

- `Mixed Release Coordinator`
- `Mixed Engineering Writer`
- `Mixed Contract Checker Bridge`
- `Mixed Risk Reviewer Bridge`

## Install

From the repo root:

```bash
uv sync --extra crewai --extra a2a
```

## Start everything

Run each component in its own terminal.

### 1. Start the external A2A services

Terminal 1:

```bash
uv run examples/mixed/03_fact_checker_a2a.py
```

Terminal 2:

```bash
uv run examples/mixed/04_risk_reviewer_a2a.py
```

Optional smoke checks before bridging:

```bash
curl http://127.0.0.1:10121/.well-known/agent.json
curl http://127.0.0.1:10122/.well-known/agent.json
```

At this point they are still external A2A services only. They are not Thenvoi participants yet.

### 2. Start the bridge process

Terminal 3:

```bash
uv run examples/mixed/05_a2a_bridge.py
```

This one process starts two Thenvoi bridge agents:

- one for the contract checker service at `http://127.0.0.1:10121`
- one for the risk reviewer service at `http://127.0.0.1:10122`

### 3. Start the CrewAI agents

Terminal 4:

```bash
uv run examples/mixed/01_strategy_coordinator.py
```

Terminal 5:

```bash
uv run examples/mixed/02_draft_writer.py
```

## Use the room

Once all five processes are running:

1. Create a Thenvoi room.
2. Add these four Thenvoi agents to the same room:
   - the release coordinator
   - the engineering writer
   - the contract checker bridge
   - the risk reviewer bridge
3. Send a request such as:

```text
We are about to ship an SDK change that adds auth headers to the A2A bridge and
updates the gateway onboarding flow. I need a release-readiness note for the
team: what changed, what config or docs need to be updated, what can break for
existing users, and what we should watch after deploy.
```

## What a successful run looks like

You should see the room split into distinct roles:

- the coordinator frames the engineering task and assigns the room
- the contract checker bridge posts API, config, test, and doc-surface details that came from the external A2A service
- the risk reviewer bridge posts compatibility, rollout, rollback, and observability concerns that came from the external A2A service
- the writer posts a final engineering handoff note with concrete next steps

From the platform's point of view, the two bridged A2A services behave like normal participants.

## Troubleshooting

### The A2A services are up, but nothing appears in Thenvoi

The bridge is the missing piece. Starting `03_fact_checker_a2a.py` and `04_risk_reviewer_a2a.py` alone does not connect them to the platform.

### The bridge starts, but the A2A services do not answer

Check that:

- `http://127.0.0.1:10121/.well-known/agent.json` works
- `http://127.0.0.1:10122/.well-known/agent.json` works
- the bridge process is using the right `MIXED_FACT_URL` and `MIXED_RISK_URL`

### The CrewAI agents start but do not respond

Check that your model credentials are available in `.env` and that the two CrewAI agents were added to the room.

### You want different ports

Override them before starting the scripts:

```bash
MIXED_FACT_PORT=11121 uv run examples/mixed/03_fact_checker_a2a.py
MIXED_RISK_PORT=11122 uv run examples/mixed/04_risk_reviewer_a2a.py
MIXED_FACT_URL=http://127.0.0.1:11121 \
MIXED_RISK_URL=http://127.0.0.1:11122 \
uv run examples/mixed/05_a2a_bridge.py
```
