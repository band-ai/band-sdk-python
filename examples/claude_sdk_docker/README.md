# Thenvoi Agent Runner

Run AI agents powered by Claude SDK with Docker - no coding required.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/)

## Quick Start

### 1. Configure your environment

From the **repository root** directory:

```bash
cp .env.example .env
# Edit .env - add your ANTHROPIC_API_KEY
```

> **Note:** This example uses the root `.env` file (not a local one).

### 2. Create your agent

1. Go to the [Thenvoi Dashboard](https://app.thenvoi.com/dashboard)
2. Log in or create an account
3. Create a new **External Agent** (see [Creating an External Agent](https://docs.thenvoi.com/getting-started/connect-external-agent) for detailed instructions)
   - **Name**: e.g., "Customer Support Bot" or "Research Assistant"
   - **Description**: e.g., "Handles customer inquiries and provides product information"
4. Copy your **Agent ID** and **API Key** from the agent settings
5. Navigate to the example directory and copy `example_agent.yaml` to a new file:

```bash
cd examples/claude_sdk_docker
cp example_agent.yaml agent1.yaml
```

6. Edit your new agent file and paste your credentials:

```yaml
agent_id: "agt_abc123xyz"  # Your Agent ID from Thenvoi
api_key: "sk_live_..."     # Your API Key from Thenvoi
```

You can create multiple agents by repeating these steps with different files (e.g. `agent2.yaml`, `agent3.yaml`).

> **Note:** Files matching `agent*.yaml` are git-ignored to protect your credentials. Only `example_agent.yaml` is tracked.

### 3. Update docker-compose.yml (optional)

**For a single agent:** No changes needed! The default configuration already uses `agent1.yaml`.

**For multiple agents:** Uncomment and add additional agent entries in `docker-compose.yml`:

```yaml
services:
  agent1:
    <<: *agent-base
    environment:
      AGENT_CONFIG: /app/config/agent1.yaml

  # Uncomment and duplicate for additional agents:
  # agent2:
  #   <<: *agent-base
  #   container_name: thenvoi-agent2
  #   environment:
  #     AGENT_CONFIG: /app/config/agent2.yaml
```

Add as many agent entries as you created in step 2.

### 4. Build and run

From the `examples/claude_sdk_docker` directory:

```bash
# Build the Docker image
docker compose build

# Run the agent
docker compose up
```

> **Important:** Docker commands must be run from `examples/claude_sdk_docker/` directory where `docker-compose.yml` is located.

## Files

| File | Description |
|------|-------------|
| `example_agent.yaml` | Template for agent configuration (copy this to create your agents) |
| `docker-compose.yml` | Docker configuration (only agents defined here will run) |
| `Dockerfile` | Docker image definition (builds from SDK source) |
| `runner.py` | Agent runner script (reads YAML config) |
| `tools/` | Custom tools for your agent |
| `entrypoint.sh` | Container entrypoint (configures git safe.directory at runtime) |

> **Note:** Environment variables are loaded from the root `.env` file. Copy `.env.example` to `.env` in the repository root.

## Agent Configuration

```yaml
# Required: credentials from Thenvoi Dashboard
agent_id: "agt_abc123xyz"
api_key: "sk_live_..."

# Optional: customize your agent.  Omit `model` to use the npm `claude`
# binary's default; aliases like `opus` / `sonnet` / `haiku` resolve to the
# latest of that family.  `fallback_model` is used by the binary when the
# primary model is unavailable.
# model: opus
# fallback_model: sonnet
prompt: |
  You are a helpful assistant that specializes in customer support.
  Be friendly, concise, and always offer to help further.
tools:
  - calculator
  - get_time

# Optional: extended thinking (requires Sonnet 4.5+ or any Opus)
# thinking_tokens: 10000
```

> **Note:** The `thinking_tokens` option enables extended thinking and requires Sonnet 4.5 or newer (any Opus also works). Use `model: sonnet` or `model: opus` to always pick the latest available.

## Multiple Agents

To run multiple agents, repeat steps 2-3 for each agent:

1. Create a new external agent on Thenvoi and copy the credentials
2. Copy `example_agent.yaml` to a new file (e.g. `agent2.yaml`)
3. Edit the new file with the credentials
4. Add the new agent to `docker-compose.yml`
5. Run: `docker compose up`

## Custom Tools

Edit `tools/example_tools.py` to add your own tools:

```python
@tool("my_tool", "Description", {"param": str})
async def my_tool(args: dict) -> dict:
    return {"content": [{"type": "text", "text": args["param"]}]}
```

Then enable in `tools/__init__.py` and add to your agent config.

## Commands

Run all commands from the `examples/claude_sdk_docker` directory:

```bash
docker compose build        # Build the image
docker compose up -d        # Start in background
docker compose logs -f      # View logs
docker compose down         # Stop
docker compose restart      # Restart
```

## Mount Contract (NFR-007)

Containers require the following mount points. The runner validates these at startup and fails with an actionable error if any are missing.

| Mount Point | Purpose | Env Override | Access |
|-------------|---------|--------------|--------|
| `/workspace/repo` | Source code repository | `REPO_PATH` | rw |
| `/workspace/notes` | Agent notes and scratch space | `NOTES_PATH` | rw |
| `/workspace/state` | Agent state persistence | `STATE_PATH` | rw |

The default `docker-compose.yml` provides all required mounts. If you customize volumes, ensure all three mount points are present.

### Shared Workspace (Default)

All agents share the same mounts. This is the simplest setup for multi-agent collaboration.

Concurrency guidance for shared workspaces:
- Designate one agent (typically the planner) as the primary writer
- Other agents should read code but coordinate changes via chat
- Use Thenvoi messaging to coordinate file modifications between agents

### Isolated Workspaces

For isolated per-agent workspaces, override mounts per service using separate directories or git worktrees:

```yaml
services:
  reviewer:
    <<: *agent-base
    volumes:
      - ./:/app/config:ro
      - ${REPO_PATH:-.}/worktrees/reviewer:/workspace/repo
      - ./data/notes/reviewer:/workspace/notes
      - ./data/state/reviewer:/workspace/state
    environment:
      AGENT_CONFIG: /app/config/reviewer.yaml
      WORKSPACE: /workspace/repo
      GIT_SAFE_DIRS: /workspace/repo
```

### Additional Writable Roots (NFR-007c)

The `GIT_SAFE_DIRS` environment variable accepts a comma-separated list of additional directories to mark as git-safe at container startup. Use this for worktree directories, clone targets, or other git repositories:

```yaml
environment:
  GIT_SAFE_DIRS: /workspace/repo/worktrees/feature-branch,/workspace/repo/clones/upstream
```
