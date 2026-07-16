# AgentCore demo — three agents orchestrating in a Band room

A working example of three AWS Bedrock AgentCore-hosted agents that
coordinate inside a single Band (Band) chatroom: `@weather`, `@math`, and
`@personal_assistant`. The personal assistant adds the other two when it
needs them, asks each a targeted question, reads their replies, and posts
a final answer back to the user. No human relay between agents.

## Other docs

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — *How it works.* The two-layer
  bridge + container shape, why the bridge has no Band logic, how
  per-room serialization and lifecycle markers prevent duplicate work,
  the AgentCore Runtime constraints to know.
- **[BUILDING.md](BUILDING.md)** — *Build your own agent.* Writing the
  system prompt, the tools available to your LLM, the per-invocation
  contract, patterns (reactive / coordinator / peer), customising the
  container, testing locally.

## Architecture

```
                    Band platform (WS + REST)
                              ▲
                              │ Phoenix WS
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   BAND_BRIDGE_AGENTS = three identities, three forwarders
        │                     │                     │
        ▼                     ▼                     ▼
   bedrock-agentcore       bedrock-agentcore     bedrock-agentcore
   :InvokeAgentRuntime     :InvokeAgentRuntime   :InvokeAgentRuntime
        │                     │                     │
        ▼                     ▼                     ▼
   ┌──────────┐         ┌──────────┐          ┌────────────────────┐
   │ weather  │         │   math   │          │ personal_assistant │
   │ container│         │ container│          │     container      │
   └──────────┘         └──────────┘          └────────────────────┘
   (Band SDK + Anthropic — one container image, different env per ARN)
```

The bridge has no Band logic. Each container runs the Band SDK against
its own `BAND_API_KEY` and uses the standard `AgentTools` surface
(`band_send_message`, `band_add_participant`, `band_lookup_peers`,
…) to operate as a first-class platform participant.

Topology: everyone in **one** Band room. PA adds peers to the user's room;
the user can see the back-and-forth.

## Prerequisites

- AWS account with Bedrock AgentCore enabled in your region (us-east-1
  recommended).
- Band platform account; three agents created on the platform — one per
  role. Note each agent's `agent_id` and `api_key`.
- An Anthropic API key.
- Docker and the `uv` Python package manager locally.

## Step 1 — Create three Band agents

On the Band platform, create three agents. Suggested handles:

- `weather`
- `math`
- `personal_assistant`

For each, give them a short description (the SDK fetches it on startup and
the platform shows it as the agent's profile). Record the `agent_id` and
`api_key`.

## Step 2 — Build the container image

The same image runs all three agents; per-agent behaviour comes from env
vars (`BAND_AGENT_ID`, `BAND_API_KEY`, `SYSTEM_PROMPT`).

```bash
# From the repo root
docker build \
    -t band-agentcore-agent:latest \
    -f examples/agentcore/Dockerfile \
    .
```

## Step 3 — Push to ECR

```bash
AWS_REGION=us-east-1
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO=band-agentcore-agent

aws ecr create-repository --repository-name "$REPO" --region "$AWS_REGION" || true
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest"
docker tag band-agentcore-agent:latest "$IMAGE"
docker push "$IMAGE"
```

## Step 4 — Create three AgentCore Runtimes

Each runtime is one agent identity. Use the `bedrock-agentcore-control`
API or the AgentCore console.

For each agent (weather, math, personal_assistant):

1. Create a runtime with the image URI from step 3.
2. Set runtime environment variables:
   - `BAND_AGENT_ID` — that agent's Band UUID.
   - `BAND_API_KEY` — that agent's Band API key.
   - `ANTHROPIC_API_KEY` — your Anthropic key.
   - `ANTHROPIC_MODEL` — e.g. `claude-sonnet-4-5-20250929`.
   - `SYSTEM_PROMPT` — paste contents of
     `examples/agentcore/agents/<agent>.txt`.
3. Capture each runtime's ARN.

Tip: rotate the Anthropic key into AgentCore Identity's credential vault
rather than a plain env var if you prefer.

## Step 5 — Configure the bridge

The bridge needs three (agent_id, api_key, target) tuples — one per agent.
Compose them into the `BAND_BRIDGE_AGENTS` JSON env var. Use
`examples/agentcore/agent_config.yaml.example` as a checklist (copy it to
`agent_config.yaml`, which is gitignored, and fill in your values).

```bash
export BAND_BRIDGE_AGENTS='[
  {
    "agent_id": "<weather agent_id>",
    "api_key": "<weather api_key>",
    "target": {
      "type": "agentcore",
      "runtime_arn": "<weather runtime ARN>",
      "region": "us-east-1"
    }
  },
  {
    "agent_id": "<math agent_id>",
    "api_key": "<math api_key>",
    "target": {
      "type": "agentcore",
      "runtime_arn": "<math runtime ARN>",
      "region": "us-east-1"
    }
  },
  {
    "agent_id": "<personal_assistant agent_id>",
    "api_key": "<personal_assistant api_key>",
    "target": {
      "type": "agentcore",
      "runtime_arn": "<personal_assistant runtime ARN>",
      "region": "us-east-1"
    }
  }
]'

# AWS credentials for InvokeAgentRuntime
export AWS_DEFAULT_REGION=us-east-1
# (or use a profile / instance role)
```

## Step 6 — Run the bridge

```bash
# Install bridge deps
uv sync --extra bridge_agentcore

# Start the bridge (loads .env automatically)
uv run python examples/agentcore/run_agentcore.py
# or run the module directly:
PYTHONPATH=band-bridge uv run python -m bridge_core
```

Watch the logs; you should see three agents connect to Band's WS, each
subscribed to its own rooms. The bridge health endpoint is at
<http://localhost:8080/health> and reports per-agent connection state.

> **Local dev with AWS SSO / `aws login`:** if you authenticate locally via
> the `login` credential provider, the bridge's first `InvokeAgentRuntime`
> call fails with `MissingDependencyException: ... botocore[crt]`. Install the
> extra once (`uv pip install "botocore[crt]"`) and restart the bridge — the
> boto3 client is built lazily and cached, so a restart is required. This is a
> local-only papercut; in a real deployment the bridge runs with an instance
> role and doesn't hit it.

## Step 7 — Try it

In the Band UI:

1. Open (or create) a room with **yourself** + `@personal_assistant`.
2. Ask: *"What is the temperature difference now, in percents, between
   Tel Aviv and Warsaw?"*
3. Watch `@personal_assistant` add `@weather` and `@math` to the room, ask
   them, read their replies, and post the final answer to you.

You can run this in parallel rooms — the bridge handles each agent's room
subscriptions independently, and `runtimeSessionId` is derived from the
room id so each room gets its own AgentCore microVM.

## Notes

- AgentCore Runtime caps a session at 8 hours and times out at 15 minutes of
  idle. The demo flow runs in seconds, so the caps don't bite — but a single
  long-running orchestration would.
- Per-event re-invocation: when a peer replies, the bridge re-invokes
  `@personal_assistant`'s container. The SDK fetches fresh room history each
  time, so the PA reasons over the full transcript on every turn.
- The bridge runs no Band logic: it just forwards WS events to the
  container's HTTP endpoint. All semantics — mention parsing, sending
  messages, looking up peers — happen inside the container via the SDK.

## Files

| Path | Role |
|---|---|
| `README.md` | This file — operator-focused deploy guide. |
| `ARCHITECTURE.md` | Conceptual overview — bridge vs container, lifecycle, constraints. |
| `BUILDING.md` | Guide for writing your own AgentCore agent. |
| `agentcore_llm_server.py` | The container (FastAPI + SDK). Runs once per ARN. |
| `Dockerfile` | Container image build. |
| `run_agentcore.py` | Local bridge launcher (loads `.env.test`). |
| `verify_deployment.py` | Post-deployment smoke check: run once after deploying to confirm the live system orchestrates (drives a live platform; not run in CI). See below. |
| `agents/*.txt` | Per-agent system prompts; paste into `SYSTEM_PROMPT`. |
| `agent_config.yaml.example` | Bridge config template — copy to `agent_config.yaml` (gitignored) and fill in values that flow into the JSON env var. |

## Verifying the deployment

Once the three runtimes and the bridge are up, confirm the whole orchestration
works end-to-end with the bundled checker. It connects as a **user**, drives the
acceptance scenario (PA recruits `@weather` and `@math`, posts a synthesized
answer), and then runs two rooms concurrently to confirm per-room session
isolation. It exits non-zero if any check fails.

```bash
AGENTCORE_DEMO_PA_AGENT_ID=<personal_assistant-agent-uuid> \
    uv run python examples/agentcore/verify_deployment.py
```

Requires `BAND_REST_URL`, `BAND_WS_URL`, and `BAND_API_KEY_USER` (read from the
environment or `.env.test`). This is manual verification tooling — it needs the
deployed demo, so it is not part of CI.

## Where to look when something is off

- Bridge says "connected and listening" but no events arrive → confirm
  each agent identity is added to the room you're testing in.
- AgentCore Runtime returns 5xx → check CloudWatch logs for that runtime.
  The container's `/ping` should be 200; `/invocations` should accept POSTs.
- PA never asks peers → check the SYSTEM_PROMPT was set on the PA's runtime
  and that the peers' agent handles match what the prompt references.
- Two agents in the same room can't hear each other → was a known kill-shot
  bug pre-INT-506. If you see it again, check that each agent has its own
  entry (with its own agent_id) in `BAND_BRIDGE_AGENTS`.
