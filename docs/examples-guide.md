# Example Files (examples/ directory)

## PEP 723 Script Metadata (Required for `uv run` support)

Every example file must include PEP 723 inline script metadata at the top for standalone execution with `uv run`:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[<extra>]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Brief description of what this example does.

Run with:
    uv run examples/<framework>/<example_file>.py
"""
```

Replace `<extra>` with the appropriate framework extra (e.g., `langgraph`, `anthropic`, `crewai`, `claude-sdk`, `pydantic-ai`, `parlant`).

## Other Requirements

- Use `load_agent_config("agent_name")` for credentials, NOT direct `os.environ.get()`
- Never read `BAND_WS_URL`/`BAND_REST_URL` by hand — `Agent.create`/`from_config`
  resolve them (explicit arg > env > production default via
  `band.config.PlatformSettings`); just call `load_dotenv()` and omit
  `ws_url`/`rest_url` (guarded by `tests/example_agents/test_surface_guards.py`)
- Run the agent with `async with agent: await agent.run_forever()` — the
  lifecycle style all examples showcase (`await agent.run()` is equivalent)
- Use `raise ValueError(...)` for missing required config, NOT `logger.error()` + `sys.exit()`
- Use single sys.path line: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`
- Never hardcode UUIDs in docstrings - reference `agent_config.yaml` instead
- All `async def main()` functions must have `-> None` return type hint
- Always include `from __future__ import annotations` as first import
