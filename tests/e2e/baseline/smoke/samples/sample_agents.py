"""Driving instructions + matrix glue for the smoke tests.

Adapter construction and discovery live in the toolkit registry
(``toolkit.adapters``); this module is the pytest-facing glue over it: the shared
role-setting prompt, ``memory_features()``, and the reusable agent *shapes*
(``TOOL_AGENT`` / ``MEMORY_AGENT``) passed as ``@per_adapter(..., **SHAPE)`` /
``@with_adapters(..., **SHAPE)``. The decorators themselves (``per_adapter`` /
``with_adapters`` / ``adapter_params``) live in ``tests.e2e.baseline.agents``. Adding a
framework is a single ``@adapter`` entry in the registry -- with one exception:
``USAGE_EXCLUSIONS`` below names adapters that can't report per-turn usage, so a
framework in that position needs an entry there too.

Following ``sample_tools``/``test_tool_calls``, the agent gets a fixed
role-setting system prompt and the *user message* carries the instruction (with
the unique marker). Each instruction forces exactly one observable action --
``band_send_event`` for an event, ``band_store_memory`` for a memory, the only
way to produce it -- so a precise instruction is the only way to comply.
"""

from __future__ import annotations

import struct
import uuid
import zlib


from band.core.types import AdapterFeatures, Capability, Emit, MessageType
from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.task_types import TaskAssignmentStatus

from tests.e2e.baseline.agents import Adapter, ExcludedAdapter
from tests.e2e.baseline.smoke.samples.sample_tools import LOOKUP_PROMPT
from tests.e2e.baseline.toolkit.observations import ContactTool, MemoryTool, TaskTool

# Fixed role-setter: the actionable instruction (and marker) travels in the user
# message, exactly like the opaque-tool smokes.
TOOL_AGENT_SYSTEM_PROMPT = (
    "You are under test. When the user messages you, do exactly what they ask: "
    "make the requested tool call(s) with the given arguments and nothing else. "
    "Do not send a chat message unless explicitly asked."
)


def memory_features() -> AdapterFeatures:
    """Features for the memory smokes: expose the memory tools, and record the
    tool call as a ``tool_call`` event so the call layer is observable."""
    return AdapterFeatures(capabilities={Capability.MEMORY}, emit={Emit.TOOL_CALLS})


def contacts_features() -> AdapterFeatures:
    """Features for contacts smokes: expose contact tools and record their calls."""
    return AdapterFeatures(capabilities={Capability.CONTACTS}, emit={Emit.TOOL_CALLS})


def tasks_features() -> AdapterFeatures:
    """Features for the task-board smokes: expose task tools and record calls."""
    return AdapterFeatures(capabilities={Capability.TASKS}, emit={Emit.TOOL_CALLS})


def files_features() -> AdapterFeatures:
    """Expose room-file tools and persist their calls for capability smokes."""
    return AdapterFeatures(capabilities={Capability.FILES}, emit={Emit.TOOL_CALLS})


def usage_features() -> AdapterFeatures:
    """Features for the cost/token smokes: emit each turn's token usage as a
    ``usage`` event so the ``Usage`` observation layer is populated."""
    return AdapterFeatures(emit={Emit.USAGE})


# A plain reply-eliciting prompt for the cost smokes: the turn just needs to run
# an LLM call (input tokens) and produce a reply (output tokens); no tools.
COST_AGENT_SYSTEM_PROMPT = (
    "You are a helpful assistant in a chat room. Reply directly to the user with "
    "one short, friendly sentence."
)


# A cost-smoke prompt for the multi-turn non-cumulative check: it must let the
# *user* dictate reply length so the test can drive one LONG turn then one TINY
# turn. That asymmetry is what makes the check robust — a correct per-turn record
# has the tiny turn's output far below the long turn's, while a cumulative bug
# (a running total) makes the second record ~= long + tiny, i.e. ~= the long
# turn. Comparing a long turn against a tiny one is a scale-immune "small vs
# large" split, unlike a "1x vs 2x" ratio of two equal turns, whose margin
# collapses under ordinary LLM reply-length variance.
COST_MULTI_TURN_SYSTEM_PROMPT = (
    "You are a helpful assistant in a chat room. Follow the user's instructions "
    "about reply length exactly: when they ask for detail, write several full "
    "paragraphs; when they ask for a single word, reply with just that one word "
    "and nothing else."
)


# A memory prompt for the *inference* smoke: a generic secretary persona that
# remembers what the user shares, deliberately WITHOUT spelling out memory
# mechanics (scope/subject_id/tool). Where ``TOOL_AGENT_SYSTEM_PROMPT`` says "use
# the given arguments" — which defeats inference — this leaves scope classification
# and identity resolution entirely to the adapter's auto-injected memory guidance.
MEMORY_SECRETARY_PROMPT = (
    "You are the user's personal secretary in a chat room. When the user shares "
    "something worth remembering, save it with your memory tools so you can recall "
    "it later. Decide for yourself how to scope and attribute each memory. Keep "
    "replies to one short sentence."
)


# Reusable agent shapes for ``@with_adapters(..., **SHAPE)``: the prompt (and
# features) a smoke runs its agents under. Declared once here so every test shares
# the same shape instead of re-spelling it.
TOOL_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT}
MEMORY_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT, "features": memory_features()}
CONTACTS_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT, "features": contacts_features()}
TASK_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT, "features": tasks_features()}
FILES_AGENT = {"prompt": TOOL_AGENT_SYSTEM_PROMPT, "features": files_features()}
MEMORY_SECRETARY_AGENT = {
    "prompt": MEMORY_SECRETARY_PROMPT,
    "features": memory_features(),
}
COST_AGENT = {"prompt": COST_AGENT_SYSTEM_PROMPT, "features": usage_features()}
COST_MULTI_TURN_AGENT = {
    "prompt": COST_MULTI_TURN_SYSTEM_PROMPT,
    "features": usage_features(),
}

# Adapters excluded from every per-turn-usage gate (the usage smokes and the
# restart usage split). Keep this registry-derived fan honest: only adapters
# unable to observe per-turn usage belong here.
USAGE_EXCLUSIONS = (
    ExcludedAdapter(Adapter.CREWAI_FLOW, "usage lives in user-supplied flow internals"),
    ExcludedAdapter(
        Adapter.CREWAI, "deferred: cumulative-lifetime counter, not per-turn"
    ),
    ExcludedAdapter(Adapter.COPILOT_ACP, "ACP exposes no per-turn token-usage updates"),
)


# Reply-oriented driving glue shared by the context-recall and rehydration
# scenarios: a prompt that answers in chat (acknowledge on "remember", state the
# value on "recall"), plus the two user messages that state and later ask for a
# note. Kept here (not inline in one test) so every recall/rehydration test drives
# the model the same way — a fair, single-source comparison across the matrix.
# Wording note: a neutral "note", not a "secret code" — models refuse to echo a
# credential-shaped value, an unrelated false failure.
REPLY_PROMPT = (
    "You are a helpful assistant in a chat room. Reply directly with one short "
    "sentence. When asked to remember something, acknowledge it; when later asked "
    "what it was, state it exactly."
)
REMEMBER = "Please remember this note: {note}. Confirm you remember it."
RECALL = "What was the note I asked you to remember? Reply with just it."


def liveness_probe(marker: str) -> str:
    """User message asking the agent to echo ``marker`` to confirm it is still
    processing — the tolerant liveness check after churn (e.g. a flood).

    Phrased as a benign confirmation rather than a terse override ("reply with just
    the word X and nothing else"), which safety-tuned models sometimes refuse
    ("I can't follow instructions that override my behaviour") — an unrelated false
    failure. The marker still lands verbatim in the reply for a substring assert."""
    return f"To confirm you're still active, please reply with the word {marker}."


def unique_marker(prefix: str) -> str:
    """A high-entropy token to assert verbatim in event/memory content."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def file_round_trip_instruction(marker: str) -> str:
    """Drive one real text-file send, discovery, and read-back in a room.

    Numbered steps plus an explicit anti-shortcut clause: a smaller model
    otherwise pattern-matches "reply with the exact token" (step 4) as the
    whole task and jumps straight to a single band_send_message, skipping
    the file round-trip entirely -- observed live against gpt-5.4-mini and
    claude-haiku-4-5.
    """
    return (
        "Complete these four tool calls in order, then stop -- do not reply "
        "or take any other action until all four are done:\n"
        f"1. Call band_send_room_file to upload a text file named "
        f"evidence.txt whose entire content is the exact token {marker}. "
        "Include at least one room participant in its mentions.\n"
        "2. Call band_list_room_files to find that file.\n"
        "3. Call band_read_room_file with its returned id.\n"
        f"4. Only after steps 1-3 are done, call band_send_message to reply "
        f"with the exact token {marker}."
    )


# Visually distinct, single-word-nameable colors for the image vision-passthrough
# smoke. Randomizing per run (see IMAGE_COLORS usage) means a model can't pass by
# reflexively guessing a common default (e.g. "blue") without actually seeing the
# uploaded pixels -- the judge is told the true color as ground truth.
IMAGE_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (220, 20, 20),
    "green": (20, 180, 20),
    "orange": (240, 140, 20),
    "purple": (140, 20, 200),
    "yellow": (230, 210, 20),
    "cyan": (20, 200, 210),
}


def solid_color_png(color_name: str, *, size: int = 64) -> bytes:
    """A minimal, real, valid solid-color PNG -- pure stdlib (no Pillow, which
    is only a crewai-extra dependency, not available in every lane's venv).

    ``color_name`` must be a key of ``IMAGE_COLORS``. Encodes an 8-bit RGB
    image (color type 2, no filtering) with a single IDAT chunk -- the
    smallest structure a real PNG decoder (and a real vision model) accepts.
    """
    rgb = IMAGE_COLORS[color_name]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw_row = b"\x00" + bytes(rgb) * size  # filter-type-0 byte + RGB pixels
    raw = raw_row * size
    idat = zlib.compress(raw)
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def image_round_trip_instruction() -> str:
    """Drive discovery and vision passthrough of an image a peer already
    shared in the room: list, read, then report what color it saw.

    No marker/color hint in the wording -- the whole point is that the model
    must actually look at the image, not echo a string. The judge checks the
    reply against the real uploaded color (known to the test, not the model)
    as ground truth.
    """
    return (
        "A peer already shared an image file in this room. Use "
        "band_list_room_files to find it, then band_read_room_file with its "
        "returned id to view it. Look at the image and identify its single "
        "dominant color in one word. Then use band_send_message to reply "
        "with just that color word."
    )


def emit_event_instruction(event_type: MessageType, marker: str) -> str:
    """User message forcing exactly one ``band_send_event`` of ``event_type``
    whose content carries ``marker`` verbatim."""
    return (
        f"Call the tool band_send_event exactly once with "
        f"message_type='{event_type.value}' and content that includes the exact "
        f"token {marker} (verbatim). That tool call is your ONLY action -- do not "
        "reply with a chat message and do not call any other tool. A plain-text "
        "reply does not satisfy this; you must call band_send_event."
    )


def emit_thoughts_instruction(markers: list[str]) -> str:
    """User message forcing one ``band_send_event`` thought per marker (used to
    demonstrate a count-floor assertion)."""
    tokens = ", ".join(markers)
    return (
        f"Call the tool band_send_event once for each of these tokens: {tokens}. "
        f"Each call uses message_type='{MessageType.THOUGHT.value}' with content "
        "containing that exact token verbatim. Those tool calls are your ONLY "
        "action -- do not reply with a chat message and do not call any other "
        "tool. A plain-text reply does not satisfy this."
    )


def store_memory_instruction(marker: str) -> str:
    """User message forcing one agent-scoped ``band_store_memory`` whose
    content carries ``marker`` verbatim, with an exact valid system/type combo."""
    return (
        "Call band_store_memory exactly once with these exact arguments: "
        f"content = a short sentence that includes the exact token {marker}; "
        f"system = {MemorySystem.LONG_TERM.value}; "
        f"type = {MemoryType.SEMANTIC.value}; "
        f"segment = {MemorySegment.USER.value}; "
        f"scope = {MemoryStoreScope.AGENT.value}; "
        "thought = a brief reason. Do not include subject_id. Do not call any "
        "other tool."
    )


def list_contacts_instruction() -> str:
    """Drive a real contacts read and finish the turn with a Band reply."""
    return (
        f"First call {ContactTool.LIST.value} to inspect your contacts. Then use "
        "band_send_message to briefly confirm that you checked them. Do not call "
        "any other tools."
    )


def task_lifecycle_instruction(marker: str) -> str:
    """User message forcing a full task-board write lifecycle in one turn: create
    a task whose subject carries ``marker``, claim it (status=in_progress), then
    complete it (status=completed) -- exercising create and update twice."""
    return (
        f"Call {TaskTool.CREATE.value} exactly once with subject including the "
        f"exact token {marker}. Then call {TaskTool.UPDATE.value} with that "
        f"task's id and status='{TaskAssignmentStatus.IN_PROGRESS.value}'. Then "
        f"call {TaskTool.UPDATE.value} again with the same id and "
        f"status='{TaskAssignmentStatus.COMPLETED.value}' and a brief comment. "
        "Do not call any other tool."
    )


def task_read_instruction() -> str:
    """Second-turn user message forcing the four task-board read tools against
    the task already created earlier in this conversation (the agent has that
    task's id/number in its own context; no marker is needed to identify it)."""
    return (
        f"Now call {TaskTool.LIST.value} to see the board. Then call "
        f"{TaskTool.GET.value} on the task you created earlier. Then call "
        f"{TaskTool.GET_HISTORY.value} on that same task. Then call "
        f"{TaskTool.GET_BOARD.value}. Do not call any other tool."
    )


def task_board_delegation_instruction(
    lookup_name: str,
    lookup_id: str,
    lookup_key: str,
    weather_name: str,
    weather_id: str,
    weather_place: str,
) -> str:
    """Coordinator's turn-1 instruction for the task-board delegation flow: set
    the room goal, create one task per specialist, then hand both off in a
    single message that states each task's number or id explicitly -- the
    specialists need it to know which task to claim and update, a real
    reliability dependency this instruction must not leave implicit."""
    return (
        f"First call {TaskTool.SET_BOARD.value} to set this room's goal: a "
        "short title and summary describing that the team needs an access "
        f"code and a weather forecast gathered. Then call "
        f"{TaskTool.CREATE.value} twice to create two tasks: one with subject "
        f"asking for the access code for key '{lookup_key}', and one with "
        f"subject asking for the forecast for '{weather_place}'. Then send "
        "exactly ONE band_send_message that mentions both "
        f"{lookup_name} (id {lookup_id}) and {weather_name} (id {weather_id}), "
        "stating the exact task number or id you just created for each of them "
        "by name, and asking each to claim their task, gather their value, and "
        "record it on the task board. Do not look anything up yourself, and do "
        "not call any other tool."
    )


def store_subject_memory_instruction(marker: str, subject_id: str) -> str:
    """User message forcing one subject-scoped ``band_store_memory`` about
    ``subject_id`` whose content carries ``marker`` verbatim."""
    return (
        "Call band_store_memory exactly once with these exact arguments: "
        f"content = a short sentence that includes the exact token {marker}; "
        f"system = {MemorySystem.LONG_TERM.value}; "
        f"type = {MemoryType.SEMANTIC.value}; "
        f"segment = {MemorySegment.AGENT.value}; "
        f"scope = {MemoryStoreScope.SUBJECT.value}; "
        f"subject_id = {subject_id}; "
        "thought = a brief reason. Do not call any other tool."
    )


def store_subject_memory_inferred_instruction(marker: str) -> str:
    """Generic user message to remember a personal fact: a natural-language
    "about me" intent that names no scope enum, no subject_id, and no tool.

    The agent must map "about me" to subject scope and look up *whose* subject it
    is (via ``band_get_participants``/``band_lookup_peers``) from the adapter's
    injected memory guidance alone — unlike ``store_subject_memory_instruction``,
    which hands it the scope and the literal ``subject_id``. Deliberately avoids
    naming (or contrasting) the organization scope, which would spell out the
    very classification under test.
    """
    return (
        "Please remember this about me personally so you can recall it later: "
        f"my project code phrase is {marker}."
    )


def supersede_memory_instruction(marker: str) -> str:
    """User message forcing a store-then-supersede lifecycle in one turn: store an
    agent-scoped memory carrying ``marker``, then supersede it by the id the store
    call returns."""
    return (
        f"First call {MemoryTool.STORE.value} with content including the exact "
        f"token {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}, "
        f"segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.AGENT.value}, and a brief thought. "
        f"Then call {MemoryTool.SUPERSEDE.value} with memory_id set to the id "
        "returned by the store call. Do not call any other tool."
    )


def archive_memory_instruction(marker: str) -> str:
    """User message forcing a store-then-archive lifecycle in one turn: store an
    agent-scoped memory carrying ``marker``, then archive it by the id the store
    call returns."""
    return (
        f"First call {MemoryTool.STORE.value} with content including the exact "
        f"token {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}, "
        f"segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.AGENT.value}, and a brief thought. "
        f"Then call {MemoryTool.ARCHIVE.value} with memory_id set to the id "
        "returned by the store call. Do not call any other tool."
    )


def recall_memory_instruction(marker: str) -> str:
    """User message forcing a store-then-recall flow in one turn: store an
    agent-scoped memory carrying ``marker``, then look it back up with the list and
    get tools (exercises the read-side memory tools)."""
    return (
        f"First call {MemoryTool.STORE.value} with content including the exact "
        f"token {marker}, system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}, "
        f"segment={MemorySegment.USER.value}, "
        f"scope={MemoryStoreScope.AGENT.value}, and a brief thought. "
        f"Then call {MemoryTool.LIST.value} with content_query={marker} to find "
        f"it. Then call {MemoryTool.GET.value} with memory_id set to the id of a "
        "memory the list returned. Do not call any other tool."
    )


def retrieve_memory_instruction(marker: str) -> str:
    """User message forcing retrieval of an already-stored agent memory, then a
    chat reply naming what was found -- the rehydration smoke asserts on that
    reply's text, so the instruction must explicitly ask for it rather than
    leaving the agent to infer it against a system prompt that otherwise tells
    it not to send a chat message unless asked."""
    return (
        f"Call {MemoryTool.LIST.value} with content_query={marker} to find the "
        f"memory. Then call {MemoryTool.GET.value} with memory_id set to the id "
        "of a memory the list returned. Then use band_send_message to state the "
        "exact token you found. Do not call any other tools."
    )


def store_two_memories_instruction(marker: str) -> str:
    """User message forcing two agent-scoped stores that both carry ``marker`` but
    differ in system/type, so one ``content_query=marker`` read returns both and
    the store-layer view can be sliced by dimension."""
    return (
        f"Call {MemoryTool.STORE.value} twice, both with content including the "
        f"exact token {marker} and a brief thought, both "
        f"segment={MemorySegment.USER.value} "
        f"scope={MemoryStoreScope.AGENT.value}. First store: "
        f"system={MemorySystem.LONG_TERM.value}, "
        f"type={MemoryType.SEMANTIC.value}. Second store: "
        f"system={MemorySystem.WORKING.value}, "
        f"type={MemoryType.EPISODIC.value}. "
        "Do not call any other tool."
    )


# --- Driving glue for the live matrix coverage scenarios ---------------------
# Wording for the platform-adaptation / custom-prompt / context-fidelity /
# multi-participant scenarios. Kept here (not inline per test) so every matrix
# cell drives the model identically — a fair, single-source comparison across
# frameworks. Prefer these builders over inline f-strings, and reuse
# ``unique_marker`` for every verbatim assertion token.


def custom_prompt_with_marker(marker: str) -> str:
    """Custom system prompt that keeps the opaque-lookup behaviour (so a code still
    round-trips) and requires ``marker`` in every reply, so the prompt's effect is
    checkable verbatim across turns. Reuses ``LOOKUP_PROMPT`` so the tool guidance
    stays single-source."""
    return (
        f"{LOOKUP_PROMPT} You can also use your platform tools to answer questions "
        "about who is in the room. IMPORTANT: every message you send MUST include "
        f"the exact word {marker}."
    )


# Roster probe: drives the agent to state its own name and use its platform tools
# (band_get_participants / band_lookup_peers) to report who is present and who is
# invitable — the identity + roster read.
ROSTER_PROBE = (
    "First, tell me your own name. Then use your tools to tell me, by name, who "
    "else is in this room right now, and who you could still invite that isn't "
    "here yet."
)


# Passive-roster description probe: the markers live only in peers' registered
# descriptions (never in this prompt), so quoting them without tools proves the
# always-injected participants list carried those descriptions.
PASSIVE_ROSTER_DESCRIPTIONS_PROBE = (
    "Without calling any tools, look only at the room participants list already "
    "in your context. Quote the description of every agent participant other "
    "than yourself. Include each agent's name next to their description."
)


def invite_instruction(peer_name: str, peer_id: str) -> str:
    """Drive the agent to add a peer to the room via band_add_participant (only).
    Mirrors the ``add agent (id ...)`` phrasing of the recruitment collab test."""
    return (
        f"There is an agent named {peer_name} (id {peer_id}) who is not in this room. "
        "Use band_add_participant to add them to this room."
    )


def invite_and_message_instruction(peer_name: str, peer_id: str, marker: str) -> str:
    """Drive the agent to invite a peer, then send it one directed message carrying
    ``marker`` — so the mention and the marker land in the *same* message (the coupled
    directed-message check)."""
    return (
        f"{invite_instruction(peer_name, peer_id)} Then send one band_send_message "
        f"that mentions {peer_name} and includes the exact token {marker}."
    )


def remove_participant_instruction(peer_name: str, peer_id: str) -> str:
    """Drive the agent to remove a peer from the room via band_remove_participant."""
    return (
        f"Use band_remove_participant to remove {peer_name} (id {peer_id}) from this "
        "room now."
    )


# Drives the agent to create a brand-new chat room via band_create_chatroom (the only way
# to produce one); the round-trip is then observed in the agent's own chat list, since the
# tool takes no title and adds no human participant.
CREATE_CHATROOM = (
    "Create a new, separate chat room using the band_create_chatroom tool. Making that "
    "tool call is your only action."
)


def remember_fact_instruction(fact: str) -> str:
    """One burst turn: ask the agent to remember ``fact`` (a unique marker). Terse so a
    burst of these is cheap; the later spanning recall is what's under test."""
    return f"Remember this fact for later: {fact}."


# Recall probe for the spanning-recall step: asks for the whole set so an early, a
# mid-history, and a recent fact can each be checked separately (a single-fact recall
# can't tell "kept the whole history" from "kept only a recent window").
RECALL_ALL_FACTS = (
    "List all the facts I have asked you to remember in this conversation so far, "
    "including the earliest ones. Reply with the facts themselves."
)


def delegate_to_peer_instruction(peer_name: str, peer_id: str) -> str:
    """Peer-initiated delegation: drive one agent to ask peer ``peer_name`` to confirm
    the value it just remembered, then report that reply — so it emits a real routing
    mention of the peer whose body carries the value it recalled from its own context,
    and the peer responds."""
    return (
        f"Ask {peer_name} (id {peer_id}) to confirm the value you just remembered: "
        f"send one band_send_message that mentions {peer_name} and states that exact "
        "value, then report their reply back to me."
    )
