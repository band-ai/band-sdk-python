# NOOA Examples for Band

Examples for running [NOOA](https://github.com/NVIDIA-NeMo/labs-OO-Agents)
(NVIDIA-labs Object Oriented Agents) on the Band platform through `NooaAdapter`.
See [docs/adapters/nooa.md](../../docs/adapters/nooa.md) for how the adapter works.

## Prerequisites

1. **Python 3.12 or 3.13**: NOOA does not support other versions.
2. **Dependencies**: `uv sync --extra nooa` (or `pip install band-sdk[nooa]`).
3. **Band platform**: a registered agent plus `BAND_WS_URL` / `BAND_REST_URL`.
4. **Model credentials**: these examples use `ANTHROPIC_API_KEY`. NOOA routes
   models through LiteLLM, so any LiteLLM model string works.
5. **`agent_config.yaml`**: copy `agent_config.yaml.example` from the repo root
   and fill in `nooa_agent`.

## Isolation

The model writes and runs Python inside the agent process. Run these examples in
a container or VM, never on a machine whose files or credentials matter.

## Examples

| File | Shows |
| ---- | ----- |
| `01_basic_agent.py` | A `NooaRoomAgent` subclass with per-room state and a plain-Python tool |
