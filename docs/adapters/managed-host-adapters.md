# Managed Host Adapter Configuration

Managed hosts keep their own profile data and pass the supported settings to the
adapter at construction. This page is the cross-adapter reference for that
projection; each adapter guide remains the reference for its other settings.

| Adapter | Construction | Custom instructions | Model | Reasoning / thinking |
|---|---|---|---|---|
| Claude SDK | `ClaudeSDKAdapter(...)` | `custom_section` | `model` | `max_thinking_tokens`; `reasoning_effort` is N/A |
| Codex | `CodexAdapter(config=CodexAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| Copilot SDK | `CopilotSDKAdapter(CopilotSDKAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| OpenCode | `OpencodeAdapter(config=OpencodeAdapterConfig(...))` | `custom_section` | `provider_id` and `model_id` | `variant`; `reasoning_effort` and `max_thinking_tokens` are N/A |

## OpenCode variants

OpenCode accepts an opaque, provider- and model-specific `variant` name on each
prompt. A variant can select reasoning effort when the chosen model exposes an
effort variant, but it can also be a custom provider setting. Check the running
OpenCode server's model catalog before selecting one; do not assume that a value
such as `"high"` is supported everywhere.

```python
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

adapter = OpencodeAdapter(
    config=OpencodeAdapterConfig(
        provider_id="openai",
        model_id="gpt-5",
        variant="high",  # Only when this server/model advertises it.
        custom_section="Keep updates concise.",
    )
)
```

Set `provider_id` and `model_id` together. The adapter omits a model override
unless both are present. OpenCode server credentials, server authentication, and
the variants available for a model are owned by the running OpenCode server, not
by `OpencodeAdapterConfig`. See the [OpenCode examples](../../examples/opencode/README.md)
for server setup and model discovery.
