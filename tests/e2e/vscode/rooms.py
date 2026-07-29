"""A Band room with the Copilot-in-VS-Code surface in it, driven turn by turn.

``SurfaceRoom`` is the one intent object the cells speak through: *someone says
something in the room, the driver relays it to Copilot, the agent's reply comes
back Band-side*. It hides the turn plumbing (mention wiring, prompt templating,
the captured-message wait) so a cell reads as scenario prose.

The reply wait is a predicate over captured room messages, not the baseline
``wait_for_reply`` barrier: that barrier keys on the recipient's delivery-status
PROCESSED ack, which only an SDK runtime emits — this surface posts through
band-mcp (REST only), so no delivery ack ever arrives.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent, ResourceManager
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.vscode.driver import CodeChatDriver, turn_prompt


def transcript(replies: Replies, *, peer_message: str = "") -> str:
    """The judge's view of a turn: the labeled reply, with the peer's message
    when the verdict is about engaging it (a bare reply reads as unjudgeable)."""
    lines = [f"Peer agent posted: {peer_message}"] if peer_message else []
    lines += [f"Reply from the agent under test: {reply.content}" for reply in replies]
    return "\n".join(lines)


@dataclass
class SurfaceRoom:
    """One provisioned room, its reply capture, and the turn protocol bound."""

    room_id: str
    capture: ReplyCapture
    driver: CodeChatDriver
    identity: ProvisionedAgent
    user_ops: UserOps
    resources: ResourceManager
    turn_timeout: float

    async def user_turn(
        self, message: str, *, instruction: str, new_session: bool = False
    ) -> Replies:
        """The user posts ``message`` (mentioning the agent); return the reply."""
        await self.user_ops.send_message(
            self.room_id,
            message,
            mention_id=self.identity.id,
            mention_name=self.identity.name,
        )
        return await self._agent_reply(
            sender_name="the user",
            message=message,
            instruction=instruction,
            new_session=new_session,
        )

    async def peer_turn(
        self, peer: ProvisionedAgent, message: str, *, instruction: str
    ) -> Replies:
        """A peer agent posts ``message`` (mentioning the agent); return the reply."""
        await self.resources.peer(peer).send_message(
            self.room_id,
            message,
            mention_id=self.identity.id,
            mention_name=self.identity.name,
        )
        return await self._agent_reply(
            sender_name=peer.name, message=message, instruction=instruction
        )

    async def remember(self, announcement: str, *, record: str) -> None:
        """Seed a platform memory: the user announces a fact, the agent must
        store ``record`` via ``band_store_memory``, and a Band-side barrier
        proves the record landed (separating a store failure from a retrieval
        failure in the next turn).

        The shape is load-bearing, learned from captured failures: a fresh
        session (otherwise the agent sees an earlier store in its chat context
        and acks without storing) and a hard imperative (a soft "store X and
        confirm" gets satisficed into a bare confirmation).
        """
        await self.user_turn(
            announcement,
            instruction=(
                f"Call band_store_memory now, exactly once, even if similar "
                f"records already exist — content exactly: '{record}'. "
                f"Then acknowledge it in the room."
            ),
            new_session=True,
        )
        observation = await self.capture.memory(self.identity)
        observation.stored.assert_stored(content=record)

    async def _agent_reply(
        self,
        *,
        sender_name: str,
        message: str,
        instruction: str,
        new_session: bool = False,
    ) -> Replies:
        """Relay the room message to Copilot and wait for the agent's reply."""
        mark = self.capture.messages.snapshot()
        await self.driver.submit_prompt(
            turn_prompt(
                self.room_id,
                self.identity.name,
                sender_name=sender_name,
                message=message,
                instruction=instruction,
            ),
            new_session=new_session,
        )

        def replied(_messages: list) -> bool:
            return bool(self._replies_since(mark))

        await self.capture.wait_until(replied, deadline_s=self.turn_timeout)
        return self._replies_since(mark)

    def _replies_since(self, mark: int) -> Replies:
        return self.capture.messages.since(mark).from_sender(self.identity.id)
