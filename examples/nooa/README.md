# NOOA Examples for Band

Examples for running [NOOA](https://github.com/NVIDIA-NeMo/labs-OO-Agents)
(NVIDIA-labs Object Oriented Agents) on the Band platform through `NooaAdapter`.
See [docs/adapters/nooa.md](../../docs/adapters/nooa.md) for how the adapter works.

## Prerequisites

1. **Python 3.12 or 3.13 on Linux or macOS**: NOOA does not support other
   versions or Windows.
2. **Dependencies**: from this checkout, `uv sync --extra nooa` and run an example
   with `uv run --extra nooa python examples/nooa/<file>`. Each file also carries
   inline script metadata, so `uv run examples/nooa/<file>` works on its own once
   a band-sdk release with NOOA support (5.1.0 or later) is published. Until then
   that form installs a released SDK without the adapter.
3. **Band platform**: a registered agent plus `BAND_WS_URL` / `BAND_REST_URL`.
4. **Model credentials**: these examples use `ANTHROPIC_API_KEY`. NOOA routes
   models through LiteLLM, so any LiteLLM model string works.
5. **`agent_config.yaml`**: copy `agent_config.yaml.example` from the repo root
   and fill in `nooa_agent` (and `tom_agent` / `jerry_agent` for the character pair).

## Isolation

The model writes and runs Python inside the agent process. Run these examples in
a container or VM, never on a machine whose files or credentials matter.

`Dockerfile` here builds an image with Python 3.12, the SDK from this checkout,
the examples and the shared character prompts. Inside it the model's code runs
as an unprivileged user that cannot write the app directory. Each example still
sees its own Band and model credentials, because it needs them. From the repo
root, with `agent_config.yaml` filled in:

```bash
docker build -f examples/nooa/Dockerfile -t band-nooa-examples .

# Pass the platform URLs and model key by name: docker's --env-file keeps
# quotes, and .env.test quotes some values.
set -a; . ./.env.test; set +a
export ANTHROPIC_API_KEY=...

nooa_example() {
  docker run --rm -it \
    -e BAND_REST_URL -e BAND_WS_URL -e ANTHROPIC_API_KEY \
    --mount type=bind,src="$PWD/agent_config.yaml",dst=/app/agent_config.yaml,readonly \
    band-nooa-examples "examples/nooa/$1"
}

nooa_example 01_basic_agent.py
```

For the character pair, run `nooa_example 02_tom_agent.py` and
`nooa_example 03_jerry_agent.py` in two terminals. Then add both agents to a
room and ask Tom to find Jerry.

## Examples

| File | Shows |
| ---- | ----- |
| `01_basic_agent.py` | A `NooaRoomAgent` subclass with per-room state and a plain-Python tool; tool calls only, no usage events |
| `02_tom_agent.py` | Tom the cat, from the shared character prompts; run with `03_jerry_agent.py` |
| `03_jerry_agent.py` | Jerry the mouse, Tom's counterpart; the two NOOA agents talk in one room |
| `04_custom_tools.py` | A typed `(InputModel, handler)` custom tool on `self.room`; a validation error comes back as `BandToolError` and the model retries |
