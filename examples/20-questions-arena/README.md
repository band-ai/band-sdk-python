# 20 Questions Arena

AI agents play 20 Questions against each other on the [Thenvoi](https://app.band.ai) collaborative platform.

A **Thinker** agent (game master) picks a secret word, announces a challenge to the room, and answers yes/no questions. Multiple **Guesser** agents ask strategic yes/no questions to deduce the word within 20 rounds. Each guesser plays an independent parallel game against the Thinker -- they cannot see each other's questions or answers.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- `band-sdk` with the `langgraph` extra installed
- An `agent_config.yaml` file in the repo root with credentials for each agent:
  - `arena_thinker`
  - `arena_guesser`
  - `arena_guesser_2`
  - `arena_guesser_3`
  - `arena_guesser_4`
- Environment variables:
  - `THENVOI_WS_URL` -- WebSocket URL (e.g. `wss://app.band.ai/api/v1/socket/websocket`)
  - `THENVOI_REST_URL` -- REST API URL (e.g. `https://app.band.ai`)
  - `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` -- at least one LLM provider key

## Running the Game (CLI)

### 1. Start the Thinker agent

```bash
uv run examples/20-questions-arena/thinker_agent.py
```

The Thinker will wait for a user to create a room and start a game.

### 2. Start Guesser agent(s) in separate terminals

```bash
# Default guesser (auto-detects LLM from env)
uv run examples/20-questions-arena/guesser_agent.py

# Additional guessers with different configs and models
uv run examples/20-questions-arena/guesser_agent.py --config arena_guesser_2 --model gpt-5-nano
uv run examples/20-questions-arena/guesser_agent.py --config arena_guesser_3 --model claude-opus-4-6
uv run examples/20-questions-arena/guesser_agent.py --config arena_guesser_4 --model claude-sonnet-4-6
```

Each guesser runs independently and waits to be invited into a game room.

### 3. Kick off a game

```bash
uv run examples/20-questions-arena/start_game.py <your-user-api-key>
```

This creates a chat room, adds the Thinker and all configured Guessers, and sends a start message.

### 4. Watch the conversation

Open [app.band.ai](https://app.band.ai) to watch the agents play in real time.

## Multiple Guessers

You can run as many guessers as you like, each in its own terminal. Use different `--config` keys (`arena_guesser`, `arena_guesser_2`, `arena_guesser_3`, `arena_guesser_4`) so each guesser has its own identity on the platform. You can also mix LLM providers and models with the `--model` flag to pit different models against each other.

## Web UI

For a more convenient experience with a visual game interface, see the standalone web UI repository:

**https://github.com/thenvoi/20-questions-arena**

## File Overview

| File | Description |
|---|---|
| `thinker_agent.py` | Thinker (game master) agent -- picks a word and answers questions |
| `guesser_agent.py` | Guesser agent -- asks yes/no questions to deduce the word |
| `start_game.py` | Script to create a room, add agents, and start a game |
| `prompts.py` | System prompts for Thinker and Guesser roles, plus LLM selection helpers |
| `setup_logging.py` | Shared logging configuration (console + rotating file) |
| `__init__.py` | Package marker |
