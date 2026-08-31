# Adding a New Framework Integration

When adding a new framework adapter and converter, follow this TDD workflow. Use the lowercase module name (e.g. `openai`, `gemini`) and derive the PascalCase class prefix (e.g. `OpenAI`, `Gemini`).

## Phase 1: Scaffold Source Files

1. Create converter at `src/band/converters/<framework>.py` — class `{Framework}HistoryConverter` with stub `convert()`, `set_agent_name()`, `__init__(*, agent_name=None)`. Use `from band.converters.parsing import parse_tool_call, parse_tool_result`.
2. Create adapter at `src/band/adapters/<framework>.py` — class `{Framework}Adapter` extending `SimpleAdapter[T]` with `__init__` params: `model`, `custom_section`, `history_converter`, `**features: Unpack[FeatureKwargs]`. Stub `on_message`, `on_started`, `on_cleanup`.
3. If the framework needs an external SDK, add an optional dependency group in `pyproject.toml`.

## Phase 2: Register with Conformance Infrastructure

1. Add an output adapter in `tests/framework_configs/output_adapters.py` — choose base class matching output format (`BaseDictListOutputAdapter`, `StringOutputAdapter`, `SenderDictListAdapter`, or `LangChainOutputAdapter`).
2. Register converter config in `tests/framework_configs/converters.py` — factory function, builder function returning `ConverterConfig` with behavioral flags, append to `_CONVERTER_CONFIG_BUILDERS`.
3. Register adapter config in `tests/framework_configs/adapters.py` — factory function with mocked constructor args, builder function returning `AdapterConfig`, append to `_ADAPTER_CONFIG_BUILDERS`. If the adapter builds its own tool schema objects, also set `advertised_arg_text` (see [Tool text is never written in an adapter](platform-tools.md#tool-text-is-never-written-in-an-adapter)).

## Phase 3: Run Conformance Tests (Expect Failures)

```bash
uv run pytest tests/framework_conformance/test_config_drift.py -v
uv run pytest tests/framework_conformance/test_adapter_conformance.py -v -k "<framework>"
uv run pytest tests/framework_conformance/test_converter_conformance.py -v -k "<framework>"
```

## Phase 4: Implement the Converter

In `src/band/converters/<framework>.py`, implement `convert()`: text messages as `[sender_name]: content`, own agent filtering, other agent remapping, tool events via `parse_tool_call`/`parse_tool_result`, skip thought messages, default role to `"user"`.

## Phase 5: Implement the Adapter

In `src/band/adapters/<framework>.py`: `on_started` sets agent name/description and creates client, `on_message` converts history and invokes LLM, `on_cleanup` cleans per-room state safely.

## Phase 6: Write Framework-Specific Tests

- Adapter tests in `tests/adapters/test_<framework>_adapter.py` — LLM invocation, tool execution, error handling, custom tools.
- Converter tests in `tests/converters/test_<framework>.py` — tool event format, batching, malformed input.

## Phase 7: Final Validation

```bash
uv run pytest tests/framework_conformance/ tests/framework_configs/ -v
uv run pytest tests/adapters/test_<framework>_adapter.py tests/converters/test_<framework>.py -v
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v
uv run ruff check . && uv run ruff format .
```

## Key Files Reference

| Purpose | Path |
|---|---|
| Adapter source | `src/band/adapters/<framework>.py` |
| Converter source | `src/band/converters/<framework>.py` |
| Adapter config registry | `tests/framework_configs/adapters.py` |
| Converter config registry | `tests/framework_configs/converters.py` |
| Output adapters | `tests/framework_configs/output_adapters.py` |
| Adapter conformance tests | `tests/framework_conformance/test_adapter_conformance.py` |
| Converter conformance tests | `tests/framework_conformance/test_converter_conformance.py` |
| Config drift detection | `tests/framework_conformance/test_config_drift.py` |
