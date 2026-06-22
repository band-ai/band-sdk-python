# QA Report: pydantic_ai / 02_custom_instructions

## Summary
- **Status:** FAIL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** openai:gpt-5.4-mini
- **Agent ID:** pydantic_agent
- **Startup:** FAILED

## Errors
- Agent failed to start: 2026-06-10 15:30:29 [INFO] band.adapters.pydantic_ai: Pydantic AI adapter started for agent: QA-PAI-pydantic_agent-1921
2026-06-10 15:30:29 [INFO] band.runtime.runtime: Starting AgentRuntime for agent c1172de7-dc42-496c-9bbb-a24e2f3f5594
2026-06-10 15:30:30 [ERROR] phoenix_channels_python_client.client: Connection failed and auto_reconnect=False: server rejected WebSocket connection: HTTP 429
Traceback (most recent call last):
  File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 15:30:29 [INFO] band.adapters.pydantic_ai: Pydantic AI adapter started for agent: QA-PAI-pydantic_agent-1921
[stderr] 2026-06-10 15:30:29 [INFO] band.runtime.runtime: Starting AgentRuntime for agent c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-06-10 15:30:30 [ERROR] phoenix_channels_python_client.client: Connection failed and auto_reconnect=False: server rejected WebSocket connection: HTTP 429
[stderr] Traceback (most recent call last):
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/src/band/client/streaming/client.py", line 312, in __aenter__
[stderr]     await self.client.__aenter__()
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv-int488/lib/python3.12/site-packages/phoenix_channels_python_client/client.py", line 165, in __aenter__
[stderr]     await self._initial_connection_future
[stderr] phoenix_channels_python_client.exceptions.PHXConnectionError: Failed to connect to wss://app.band.ai/api/v1/socket/websocket?api_key=%2A%2A%2A&vsn=2.0.0: server rejected WebSocket connection: HTTP 429
[stderr] 
[stderr] The above exception was the direct cause of the following exception:
[stderr] 
[stderr] Traceback (most recent call last):
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/pydantic_ai/02_custom_instructions.py", line 78, in <module>
[stderr]     asyncio.run(main())
[stderr]   File "/Users/nirs/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/lib/python3.12/asyncio/runners.py", line 195, in run
[stderr]     return runner.run(main)
[stderr]            ^^^^^^^^^^^^^^^^
[stderr]   File "/Users/nirs/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/lib/python3.12/asyncio/runners.py", line 118, in run
[stderr]     return self._loop.run_until_complete(task)
[stderr]            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
[stderr]   File "/Users/nirs/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
[stderr]     return future.result()
[stderr]            ^^^^^^^^^^^^^^^
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/pydantic_ai/02_custom_instructions.py", line 74, in main
[stderr]     await agent.run()
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/src/band/agent.py", line 274, in run
[stderr]     await self.start()
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/src/band/agent.py", line 226, in start
[stderr]     await self._runtime.start(
... (11 more lines)
```
