"""Codex adapter configuration."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from band.adapters.codex.approvals import ApprovalDecision, SessionApprovalGranularity
from band.adapters.codex.sandbox import CodexWindowsSandbox
from band.core.adapterconfig import EnvAdapterConfig
from band.core.retention import RoomRetentionConfig, require_disabled_retention
from band.core.turn_lifecycle import TURN_SETTLE_GRACE_S, TurnSettleTimeout
from band.core.types import ApprovalMode
from band.workspaces import WorkspaceResolver, is_host_absolute

TransportKind = Literal["stdio", "ws"]


# One acquisition starts the process, refreshes ``model/list`` online and
# strictly resumes the thread's whole rollout.
CODEX_TRANSITION_TIMEOUT_S = 60.0


class WithoutCwdBinding(PydanticBaseSettingsSource):
    """Drops ``cwd``'s implicit env binding from a settings source.

    ``populate_by_name=True`` makes pydantic-settings honor a field's bare
    name as an env alias alongside any explicit ``validation_alias`` -- which
    for a field literally named ``cwd`` means the plain, generically-meaningful
    ``CODEX_CWD`` is always live, however it's aliased. ``cwd`` exists on
    ``CodexAdapterConfig`` only to reject the legacy explicit kwarg (see
    ``CodexAdapter.__init__``), so an ambient ``CODEX_CWD`` set for something
    unrelated (e.g. a Codex CLI convention, or this repo's own E2E harness)
    must never silently populate it and trip that rejection.
    """

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self._source = source

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        values = dict(self._source())
        values.pop("cwd", None)
        return values


class CodexAdapterConfig(EnvAdapterConfig):
    """Runtime configuration for Codex adapter sessions.

    Every field can be set explicitly (highest priority) or via a
    ``CODEX_``-prefixed environment variable (e.g. ``CODEX_MODEL``,
    ``CODEX_TRANSPORT``, ``CODEX_APPROVAL_MODE``); ``codex_ws_url`` is
    sourced from ``CODEX_WS_URL`` (the field name already carries the
    ``codex_`` part), ``emit_turn_task_markers`` also accepts the legacy
    ``CODEX_TURN_TASK_MARKERS``, and ``codex_command`` is sourced from the
    established ``CODEX_COMMAND`` (not the doubly-prefixed
    ``CODEX_CODEX_COMMAND``), parsed the same way as an explicit tuple: a
    whitespace-split shell string (e.g. ``"custom-codex --args"``). An
    explicit constructor kwarg always wins over the environment.

    Windows sandbox:
        Codex leaves its Windows sandbox off unless configured, and then
        treats ``workspace-write`` as read-only and, under
        ``approval_policy="never"``, refuses every command. ``windows_sandbox``
        picks a backend for each thread; unset keeps Codex's own
        ``[windows] sandbox`` config. It is opt-in because both backends
        change the host: ``unelevated`` grants ACL entries on the workspace
        and temp directory, and ``elevated`` needs administrator setup that
        creates sandbox users. Keep room workspaces under a directory with
        inherited permissions; one created with mode ``0o700`` (such as
        ``tempfile.mkdtemp``) can block the sandbox from the workspace.
        Codex does not recognize a Windows sandbox denial, so an approved
        command it first runs sandboxed is not retried outside the sandbox:
        with ``read-only``, an approved write still fails.

    Turn task events:
        ``emit_turn_task_markers`` and ``emit_turn_lifecycle_events`` are
        independent channels.  Enabling both will produce **two** task
        events per completed turn: one "Codex turn" marker and one
        enriched "Codex turn lifecycle" event (plus a "started" lifecycle
        event at turn start).  Consumers that render task timelines
        should pick exactly one channel — typically the lifecycle channel
        when its extra metadata is desired.
    """

    # populate_by_name: an aliased field stays constructible by its field name
    # and keeps its prefix-derived environment variable.
    model_config = SettingsConfigDict(
        env_prefix="CODEX_", populate_by_name=True, arbitrary_types_allowed=True
    )

    transport: TransportKind = "stdio"
    retention: RoomRetentionConfig = Field(
        default_factory=lambda: RoomRetentionConfig(
            transition_timeout_s=CODEX_TRANSITION_TIMEOUT_S
        )
    )
    model: str | None = None
    # Not validated here: the supported efforts vary by model and Codex CLI
    # version (model/list reports them), and the backend rejects unknown ones.
    reasoning_effort: str | None = None
    reasoning_summary: Literal["auto", "concise", "detailed", "none"] | None = None
    # Explicit-kwarg-only (see settings_customise_sources): rejected outright
    # in CodexAdapter.__init__ -- never populated from CODEX_CWD.
    cwd: str | None = None
    workspace_for_room: WorkspaceResolver | None = Field(default=None, exclude=True)
    approval_policy: str = "never"
    personality: Literal["friendly", "pragmatic", "none"] = "pragmatic"
    sandbox: str | None = None
    sandbox_policy: dict[str, Any] | None = None
    windows_sandbox: CodexWindowsSandbox | None = None
    system_prompt: str | None = None
    custom_section: str = ""
    include_base_instructions: bool = True
    experimental_api: bool = True
    # A validation_alias names an environment variable verbatim (env_prefix is
    # not applied), so aliases must be full CODEX_* names — an unprefixed alias
    # would read a bare env var set for something else entirely.
    emit_turn_task_markers: bool = Field(
        default=False, validation_alias=AliasChoices("CODEX_TURN_TASK_MARKERS")
    )
    approval_mode: ApprovalMode = "manual"
    approval_text_notifications: bool = True
    approval_wait_timeout_s: float = 300.0
    approval_timeout_decision: ApprovalDecision = "decline"
    turn_timeout_s: float = 180.0
    client_name: str = "band_codex_adapter"
    client_title: str = "Band Codex Adapter"
    client_version: str = "0.1.0"
    codex_command: tuple[str, ...] | None = Field(
        default=None, validation_alias=AliasChoices("CODEX_COMMAND")
    )
    codex_env: dict[str, str] | None = None
    codex_ws_url: str = Field(
        default="ws://127.0.0.1:8765", validation_alias=AliasChoices("CODEX_WS_URL")
    )
    enable_self_config_tools: bool = False
    additional_dynamic_tools: list[dict[str, Any]] = Field(default_factory=list)
    # CODEX_SKILL_ROOTS is a JSON list.
    skill_roots: list[str] = Field(default_factory=list)
    # Replay the room's transcript into a thread Codex did not resume: none
    # was recorded, resume failed, or an earlier seeding turn did not complete.
    inject_history_on_resume_failure: bool = True
    max_history_messages: int = 50
    max_pending_approvals_per_room: int = Field(default=50, ge=1)
    max_approval_audit_per_room: int = 100
    # Upper bound on session-level approvals retained per room
    # (``/approve-session`` patterns).  Evicted LRU-style when exceeded so a
    # long-lived room that approves many distinct commands can't grow this
    # set without bound.
    max_session_approved_per_room: int = 100
    # Upper bound for one attempt at closing the transport client; a close
    # that runs over stays owned and the next cleanup retries it. ``None``
    # disables the bound (legacy behavior); ``retention.transition_timeout_s``
    # still bounds how long a caller waits to learn the outcome.
    client_close_timeout_s: float | None = 10.0
    # Also bounds answering a finished turn's leftover native requests. Not
    # Optional like client_close_timeout_s: a turn possibly stuck on real work
    # must never defeat its bound.
    turn_settle_timeout_s: TurnSettleTimeout = TURN_SETTLE_GRACE_S
    # Matching rules for session-level auto-approval (``/approve-session``).
    #
    # ``"full_command"`` (default, more restrictive) — the key is the exact
    #     command string after ``.strip()`` on the single ``command`` field
    #     of the approval payload.  Matching is byte-exact and case-sensitive;
    #     only leading/trailing whitespace is normalised.  ``npm test`` does
    #     NOT match ``npm  test`` (double space), ``NPM TEST`` (case), or
    #     ``npm test --watch`` (trailing args).
    #
    # ``"binary"`` (less restrictive) — the key is just the first whitespace-
    #     delimited token of the command.  ``/approve-session`` on ``npm test``
    #     auto-approves every future command starting with ``npm`` in that
    #     room, regardless of subcommand or arguments.  Intended for
    #     trusted-binary shortcuts (``npm``, ``git``) where per-argv friction
    #     outweighs the safety margin.  Not recommended for commands that can
    #     take destructive flags (``rm``, ``git push --force``).
    #
    # File-change approvals always key on the sorted set of paths being
    # modified, independent of this flag.
    session_approval_granularity: SessionApprovalGranularity = "full_command"
    # --- Phase 2: Plan & task lifecycle ---
    stream_plan_events: bool = False
    emit_turn_lifecycle_events: bool = False
    # --- Phase 3: Real-time streaming ---
    stream_reasoning_events: bool = False
    stream_commentary_events: bool = False
    # --- Phase 4: Diffs & token usage ---
    emit_diff_events: bool = False
    emit_token_usage_events: bool = False

    @field_validator("retention")
    @classmethod
    def _require_safe_retention(
        cls, retention: RoomRetentionConfig
    ) -> RoomRetentionConfig:
        return require_disabled_retention(
            retention,
            reason=(
                "Codex idle retention is unavailable: native idle and terminal state "
                "do not account for asynchronous hooks and delegated work; shutdown "
                "can cancel that work."
            ),
        )

    @field_validator("codex_command", mode="before")
    @classmethod
    def _split_codex_command(cls, value: Any) -> Any:
        """CODEX_COMMAND is a shell string; an explicit tuple/list kwarg
        passes through unchanged."""
        if isinstance(value, str):
            return value.split()
        return value

    @field_validator("skill_roots")
    @classmethod
    def _require_absolute_paths(cls, roots: list[str]) -> list[str]:
        if relative := [root for root in roots if not is_host_absolute(root)]:
            raise ValueError(f"skill_roots must be absolute paths: {relative}")
        return roots

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep ``cwd`` constructible only via an explicit kwarg -- see
        ``WithoutCwdBinding``."""
        return (
            init_settings,
            WithoutCwdBinding(env_settings),
            WithoutCwdBinding(dotenv_settings),
            file_secret_settings,
        )
