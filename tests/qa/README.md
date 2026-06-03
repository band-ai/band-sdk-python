# QA Test Harness

End-to-end testing harness for the Band Python SDK. It maps the health of every adapter and example in the SDK by spawning real agent processes against the live platform, sending messages, and verifying correct behavior through platform-level observation.

## Purpose

The SDK supports many framework adapters (LangGraph, Google ADK, Letta, Anthropic, etc.) and ships example scripts for each. This harness answers a single question: **does each example actually work end-to-end?**

It tests the full stack — example code, SDK adapter, WebSocket transport, REST API, and LLM response — so when a scenario passes, every layer in that path is confirmed working. When it fails, the reports narrow down the suspect layer.

## What's Here

```
tests/qa/
├── README.md              ← You are here
├── INSTRUCTIONS.md        ← Quick-reference index: scenarios, adapters, timeouts, priority
├── RUN.md                 ← Setup, credentials, CLI reference
├── SCENARIOS.md           ← What each scenario tests, pass criteria, what to watch for
├── REPORTING.md           ← How to read reports, file issues, known bugs
├── EXTENDING.md           ← How to add a new adapter to the harness
├── .env.example           ← Environment variable template (platform URLs, API keys)
├── run.py                 ← Test runner CLI
├── harness/               ← Shared infrastructure (API client, agent runner, reporter)
├── scenarios/             ← Scenario implementations (A through I)
├── adapters/              ← Per-adapter config and agent scripts
│   ├── anthropic/
│   ├── claude_sdk/
│   ├── crewai/
│   ├── gemini/
│   ├── google_adk/
│   ├── langgraph/
│   ├── letta/
│   ├── parlant/
│   └── pydantic_ai/
└── reports/               ← Generated test reports (gitignored)
```

## Quick Start

```bash
cp tests/qa/.env.example tests/qa/.env           # fill in API keys
cp tests/qa/adapters/langgraph/agent_config.yaml.example \
   tests/qa/adapters/langgraph/agent_config.yaml  # fill in agent credentials

python tests/qa/run.py --adapter langgraph        # core scenarios
python tests/qa/run.py --adapter langgraph --all  # everything
python tests/qa/run.py --all-adapters             # full sweep, all adapters
```

See [RUN.md](RUN.md) for full setup instructions.
