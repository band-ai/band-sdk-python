# Platform Tools

Every Band agent gets a standard set of tools for chat, contacts, memory,
files, and the room task board. This page documents the tool inventory and
the rule that keeps their descriptions consistent across every framework
adapter.

## Tool text is never written in an adapter

`src/band/runtime/tools/` owns every word an LLM reads about a platform tool:
the input model's class docstring is the tool description, and each
`Field(description=...)` is an argument description. An adapter must reach for
whichever of these fits its framework instead of retyping the text:

| Framework wants | Use | Result |
|---|---|---|
| A Pydantic `args_schema` class | `platform_args_schema(name)` | a schema-sanitized subclass of the master model |
| The same, but its tool layer emits a value the master won't parse | `platform_args_schema(name, validators={...})` | a subclass with the master's text plus the extra validators |
| Schema derived from a function docstring | `@platform_tool` (bare — reads `fn.__name__`, takes no name argument) | docstring = master description + a rendered `Args:` section |
| A raw JSON/dict schema | `iter_tool_definitions()`, `get_openai_tool_schemas()`, `get_anthropic_tool_schemas()` | built live from the master |

None of these accept description text — an adapter that needs different wording
has a modeling problem to fix on the master, not a local string to write. If a
framework's leniency really is adapter-specific (CrewAI's `mentions` coercion),
express it as a `validators=` entry, never as a re-declared field.

```python
from band.runtime.tools import SendMessageInput, platform_args_schema, platform_tool


@platform_tool
async def band_send_message(content: str, mentions: list[str]) -> None: ...


assert issubclass(platform_args_schema("band_send_message"), SendMessageInput)
assert "Args:" in (band_send_message.__doc__ or "")
```

Guardrail: `tests/framework_conformance/test_tool_text_drift.py` runs each
`AdapterConfig.advertised_arg_text` probe and fails if what the adapter
advertises differs from the master. Wire the probe up for a new adapter that
builds its own schema objects; leave it `None` when the master schema is passed
through untouched.

## Chat Tools
- `band_send_message`: Send message to chat room (requires mentions)
- `band_send_event`: Send non-message event (thought, error, task)
- `band_add_participant`: Add agent/user to room
- `band_remove_participant`: Remove participant from room
- `band_get_participants`: List room participants
- `band_lookup_peers`: Find available agents/users
- `band_create_chatroom`: Create new chat room

## Contact Tools
- `band_list_contacts`: List agent's contacts with pagination
- `band_add_contact`: Send contact request to add someone
- `band_remove_contact`: Remove existing contact
- `band_list_contact_requests`: List received and sent requests
- `band_respond_contact_request`: Approve, reject, or cancel requests

## Memory Tools
- `band_list_memories`: List memories with filters (scope, system, type)
- `band_store_memory`: Store new memory with content, system, type, segment
- `band_get_memory`: Retrieve a specific memory by ID
- `band_supersede_memory`: Mark memory as superseded (soft delete)
- `band_archive_memory`: Archive memory (hide but preserve)

## File Tools
- `band_list_room_files`: List files attached to any message in the room, paginated
- `band_read_room_file`: Read a file — inline text/image for small previewable files, a description otherwise
- `band_send_room_file`: Upload text content as a file and share it in the room

`Capability.FILES` gates the three file tools above — see
[Capability Negotiation](capability-negotiation.md) for when it's actually
usable against a real deployment.

## Task Board Tools
- `band_list_tasks`: List the shared tasks on this room's task board
- `band_create_task`: Create a shared task on this room's task board
- `band_get_task`: Read one task by UUID or board number
- `band_update_task`: Update a task's status, active_form, comment, subject, detail, or lifecycle state
- `band_get_task_history`: The append-only history of one task
- `band_get_board`: Read this room's goal (the team mission)
- `band_set_board`: Set or update this room's goal (upsert)

`Capability.TASKS` gates the seven task-board tools above, room-scoped like
the file tools — see [Capability Negotiation](capability-negotiation.md) for
how a request gets pruned against `AgentMe.feature_flags`.
