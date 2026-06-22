# QA Test Harness

End-to-end QA for Band SDK adapters. Spawns real agent processes, connects them to the platform, sends messages via REST API, and verifies responses through platform-level observation.

**Full stack tested:** example code → SDK adapter → platform WebSocket → REST API → agent response. If one example passes, the platform and that adapter are capable — narrowing down suspects for failures.

## Files

| Doc | What's in it |
|-----|-------------|
| [RUN.md](RUN.md) | Setup, credentials, CLI reference, how to run |
| [SCENARIOS.md](SCENARIOS.md) | Scenario definitions, pass criteria, what to watch for during a run |
| [REPORTING.md](REPORTING.md) | Reading reports, filing issues, known bugs |
| [EXTENDING.md](EXTENDING.md) | Adding support for a new adapter |

## Configured Adapters

| Adapter | LLM | Core Examples | Expanded | Multi-Participant | Notes |
|---------|-----|---------------|----------|-------------------|-------|
| langgraph | gpt-4o | 01_simple, 02_custom_tools, 03_custom_personality | E, F1-F3, G, I | langgraph + letta | |
| google_adk | gemini-2.5-flash | 01_basic, 02_custom_instructions, 03_custom_tools | E, F1-F3, G, I | — | |
| anthropic | claude-sonnet-4-5 | 01_basic, 02_custom_instructions | E, F1-F3, G, I | — | |
| crewai | gpt-5.4-mini | 01_basic, 02_role_based | E, F1-F3, G, I | — | conflicts with parlant |
| gemini | gemini-2.5-flash | 01_basic | E, F1-F3, G, I | — | |
| pydantic_ai | openai:gpt-5.4-mini | 01_basic, 02_custom_instructions | E, F1-F3, G, I | — | |
| claude_sdk | claude (CLI) | 01_basic | — | — | requires Node.js + Claude Code CLI |
| parlant | openai (via Parlant) | 01_basic | — | — | conflicts with crewai; needs `dev-parlant` |
| letta | openai/gpt-5.4-mini | 01_basic | — | (paired with langgraph in D) | |

## Fastest Path

```bash
# One adapter, core scenarios
python tests/qa/run.py --adapter langgraph

# One adapter, everything
python tests/qa/run.py --adapter langgraph --all

# All adapters, full sweep
python tests/qa/run.py --all-adapters
```

## Timeouts

| Phase | Timeout | Rationale |
|-------|---------|-----------|
| Agent startup | 180s | Some adapters (letta) need >60s for dependency resolution + connection |
| LLM response | 120s | All message-wait calls use 120s+; LLMs under load can be slow |
| Rehydration pending response | 480s | Agent must reprocess entire transcript after restart |
| Contact CALLBACK | 15s | No LLM involved, just event handling |

## Priority Order

When running QA, prioritize in this order:
1. **Basic Conversation (A)** — validates the fundamental flow works
2. **Rehydration (B)** — highest bug density; replayed messages, duplicate responses
3. **Context Isolation (C)** — cross-room leakage is a critical correctness bug
4. **Multi-Participant (D)** — validates cross-adapter interop
5. **Expanded (E-I)** — feature-specific validation
