# NOOA Integration

`NooaAdapter` runs a [NOOA](https://github.com/NVIDIA-NeMo/labs-OO-Agents)
(NVIDIA-labs Object Oriented Agents) agent class in Band rooms. A NOOA agent is a
Python class: its docstring is the prompt, fields are state, plain methods are
tools, and an `async def` whose body is `...` is completed by a model that writes
and runs Python against `self`.

Install with `uv add band-sdk[nooa]`. NOOA supports Python 3.12 and 3.13 only;
on other versions the extra installs nothing.

```python
from band.adapters import NooaAdapter, NooaRoomAgent
from band.testing.nooa import room_call, scripted_llm

llm = scripted_llm(
    room_call("band_send_message", {"content": "Hi", "mentions": ["Alice"]})
)


class Greeter(NooaRoomAgent, llm=llm):
    """You are Greeter, a friendly assistant."""


adapter = NooaAdapter(agent=Greeter)
assert adapter.config.custom_section is None
```

Subclass `NooaRoomAgent` and pass the class, not an instance. Real agents pass a
LiteLLM-backed client such as `get_llm_client("anthropic/claude-sonnet-5-5")`.

These invariants are easy to break:

- **Band tools are methods on `self.room`, not tool schemas.** The model calls
  `await self.room.band_send_message(...)` from its generated code. Each tool's
  signature and docstring come from the master input model in
  `band.runtime.tools`. NOOA's view of `self` shows only the first docstring line
  of a referenced type, so the adapter also adds the full `RoomTools` reference
  as a prefix context block. Without it, the model never sees argument
  descriptions.
- **The agent class is subclassed in `on_started`.** NOOA documents `self` from
  class annotations, so the adapter subclasses the given class with `room`
  annotated as a `RoomTools` subclass that has exactly the allowed tools. That
  set depends on the effective features, which are only known after capability
  negotiation.
- **One instance per room.** A NOOA agent keeps its event log for its lifetime,
  so the room instance is its conversation memory. A session start replaces it
  and seeds the platform transcript as a prefix context block. `on_cleanup`
  drops it.
- **A tool call only works during a turn.** `self.room` is bound to the turn's
  tools while `respond` runs. A call after that, for example from a task the
  model left running, raises `BandToolError`. A failed tool call also raises
  `BandToolError` into the model's session, so the model can read it and recover.
- **The return value is never relayed.** `respond` returns `None`. A reply
  reaches the room only through `band_send_message`, and the shared turn
  judgment reports a turn that neither replied nor declined.
- **Images come back as `nooa.Image`.** An image file read through
  `band_read_room_file` returns `Image` objects the model can `show()`. The
  `tool_result` event carries a placeholder, not the bytes.

## Isolation

The model's code runs inside the agent process. NOOA's AST checks are not a
security boundary, and generated code can reach anything the process can,
including the adapter's own objects. Run NOOA agents in a container, a VM, or
NVIDIA OpenShell. NOOA's optional Linux sandbox backend
(`CodeActConfig(execution_backend="sandbox")`) adds kernel guardrails but is not
a replacement.

Importing `nooa` also installs a `SIGUSR2` debug handler, and NOOA enables
OpenTelemetry tracing when its trace viewer (`nooa start-dev`) is reachable.
