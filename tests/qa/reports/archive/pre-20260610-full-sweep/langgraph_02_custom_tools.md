# QA Report: langgraph / 02_custom_tools

## Summary
- **Status:** FAIL
- **Date:** 2026-05-26
- **Platform:** app.band.ai
- **LLM:** gpt-4o
- **Agent ID:** simple_agent
- **Startup:** FAILED

## Errors
- Agent failed to start: Traceback (most recent call last):
  File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/langgraph/02_custom_tools.py", line 117, in <module>
    asyncio.run(main())
  File "/Users/nirs/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/lib/python3.12/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "/Users/nirs/.local/share/uv/python/cpython-3.12.12-macos-aarch64-none/lib/python3.12/asyncio/runners.

## Startup Logs (excerpt)
```
[stderr] Traceback (most recent call last):
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/langgraph/02_custom_tools.py", line 117, in <module>
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
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/langgraph/02_custom_tools.py", line 105, in main
[stderr]     agent = Agent.from_config(
[stderr]             ^^^^^^^^^^^^^^^^^^
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/src/thenvoi/agent.py", line 174, in from_config
[stderr]     agent_id, api_key = load_agent_config(name, config_path=config_path)
[stderr]                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
[stderr]   File "/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/src/thenvoi/config/loader.py", line 90, in load_agent_config
[stderr]     raise ValueError(
[stderr] ValueError: Agent 'custom_tools_agent' not found in /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/langgraph/agent_config.yaml. Please add the agent configuration.
```
