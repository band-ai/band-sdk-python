# Multi-Agent Docker Compose

Run a 2-agent team (Claude SDK planner + Codex reviewer) sharing a workspace, connected to the Band platform.

## Architecture

```text
docker compose up
├── planner        (ClaudeSDKAdapter, Claude model)
│   └── Role: planner — designs plans, coordinates agents
├── reviewer       (CodexAdapter, gpt-5.5, reasoning: xhigh)
│   └── Role: reviewer — reviews plans and code, finds gaps and risks
```

Shared workspace volumes:
- `/workspace/repo` (git working tree)
- `/workspace/notes` (plan/review files)
- `/workspace/state` (repo-init lock + metadata)
- `/workspace/context` (generated context files)

## Repo Initialization

On startup, each container reads `repo` config from `agent_config.yaml`:
1. Clone repo to `repo.path` if missing
2. Skip clone when repo already exists
3. Optionally generate context files when `repo.index: true`

Generated files:
- `/workspace/context/structure.md`
- `/workspace/context/patterns.md`
- `/workspace/context/dependencies.md`

These files are injected into planner/reviewer system prompts automatically.

## Clone URL Support

`repo.url` supports both:
- SSH: `git@github.com:org/repo.git`
- HTTPS: `https://github.com/org/repo.git`

### SSH prerequisites

The compose file mounts:
- `~/.ssh` (read-only)
- `~/.gitconfig` (read-only)

When using SSH URLs:
1. Ensure your key is available in `~/.ssh`
2. Ensure `known_hosts` contains the git host
3. Optionally test on host first:

```bash
ssh -T git@github.com
```

If strict host checking is enabled (default), startup fails fast when the host key is missing.

### HTTPS prerequisites

When using HTTPS URLs, configure git credentials on host (`credential helper`, PAT, etc.) so mounted git config can authenticate.

## Prerequisites

- Docker and Docker Compose v2
- Anthropic API key for planner
- OpenAI API key for reviewer
- Band agent credentials (`agent_id` + `api_key`)

## Setup

1. Configure environment:

```bash
cp .env.example .env
```

2. Configure agents and repo:

```bash
cp agent_config.yaml.example agent_config.yaml
```

Fill in:
- `planner.agent_id`, `planner.api_key`
- `reviewer.agent_id`, `reviewer.api_key`
- `planner.repo` and `reviewer.repo` (same URL/path/branch)

3. Build and run:

```bash
docker compose build
docker compose up -d
```

4. View logs:

```bash
docker compose logs -f
docker compose logs -f planner
docker compose logs -f reviewer
```

## Configuration

### `.env`

| Variable | Default | Description |
|----------|---------|-------------|
| `BAND_REST_URL` | `https://app.band.ai` | Platform REST API |
| `BAND_WS_URL` | `wss://app.band.ai/...` | Platform WebSocket |
| `ANTHROPIC_API_KEY` | -- | Anthropic API key for planner |
| `OPENAI_API_KEY` | -- | OpenAI API key for reviewer |
| `GIT_SSH_STRICT_HOST_KEY_CHECKING` | `true` | Enforce host-key precheck for SSH remotes |
| `REPO_INIT_LOCK_TIMEOUT_S` | `120` | Max wait for repo-init lock |
| `REVIEWER_AGENT_KEY` | `reviewer` | Agent config key for reviewer |
| `REVIEWER_MODEL` | `gpt-5.5` | Model for reviewer |
| `REVIEWER_REASONING_EFFORT` | `xhigh` | Reasoning effort for reviewer |

### `agent_config.yaml`

Use `repo` under both planner and reviewer:

```yaml
planner:
  agent_id: "..."
  api_key: "..."
  role: planner
  repo:
    url: "git@github.com:org/repo.git"
    path: "/workspace/repo"
    branch: "main"
    index: true

reviewer:
  agent_id: "..."
  api_key: "..."
  repo:
    url: "git@github.com:org/repo.git"
    path: "/workspace/repo"
    branch: "main"
    index: true
```

## Troubleshooting

- `Config file not found`: create `agent_config.yaml` from the example.
- `Invalid repo configuration`: verify `repo.url` and absolute `repo.path`.
- `Host not found in known_hosts`: add host key (`ssh-keyscan -H <host> >> ~/.ssh/known_hosts`).
- `Authentication failed` on clone: verify SSH keys or HTTPS token/credential helper.
- `Timed out waiting for repo init lock`: another container may be stuck during startup; inspect logs and restart.

## Cleanup

```bash
docker compose down
docker compose down -v
```
