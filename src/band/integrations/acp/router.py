"""Agent router for slash commands and session mode-based routing."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AgentRouter:
    """Routes prompts to specific peers via slash commands or session modes.

    Supports two routing mechanisms:
    1. Slash commands: "/codex fix bug" -> routes to "codex" peer
    2. Mode mapping: mode "code" -> routes to mapped peer

    If no route matches, returns None as the target (mention all peers).

    Example:
        router = AgentRouter(
            slash_commands={"codex": "codex", "claude": "claude-code"},
            mode_to_peer={"code": "codex", "chat": "claude-code"},
        )
        text, peer = router.resolve("/codex fix the bug")
        # text = "fix the bug", peer = "codex"
    """

    def __init__(
        self,
        mode_to_peer: dict[str, str] | None = None,
        slash_commands: dict[str, str] | None = None,
    ) -> None:
        """Initialize agent router.

        Args:
            mode_to_peer: Mapping of mode_id to peer_name.
            slash_commands: Mapping of command name to peer_name.
                Commands are matched without the leading "/".
        """
        self._mode_to_peer = mode_to_peer or {}
        self._slash_commands = {
            command.lower(): peer for command, peer in (slash_commands or {}).items()
        }

    def resolve(
        self, text: str, current_mode: str | None = None
    ) -> tuple[str, str | None]:
        """Resolve routing for a prompt.

        Priority:
        1. Slash command match (e.g., "/codex fix bug")
        2. Mode mapping (if current_mode is set)
        3. Default: no specific peer (mention all)

        Args:
            text: The raw prompt text.
            current_mode: The current session mode, or None.

        Returns:
            Tuple of (cleaned_text, target_peer_name_or_none).
        """
        # Check slash commands
        if text.startswith("/"):
            parts = text[1:].split(None, 1)
            if parts:
                command = parts[0].lower()
                if command in self._slash_commands:
                    cleaned = parts[1] if len(parts) > 1 else ""
                    peer = self._slash_commands[command]
                    logger.debug(
                        "Slash command route: /%s -> peer %s",
                        command,
                        peer,
                    )
                    return cleaned, peer

        # Check mode mapping
        if current_mode and current_mode in self._mode_to_peer:
            peer = self._mode_to_peer[current_mode]
            logger.debug("Mode route: %s -> peer %s", current_mode, peer)
            return text, peer

        # Default: no specific peer
        return text, None
