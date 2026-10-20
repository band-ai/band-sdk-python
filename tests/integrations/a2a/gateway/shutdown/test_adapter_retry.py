"""A failed shutdown step stays owned by the adapter until a retry completes it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.server.events import EventQueueLegacy
from a2a.types import Task, TaskState, TaskStatus

from band.integrations.a2a.gateway import A2AGatewayAdapter
from band.integrations.a2a.gateway.types import PendingA2ATask


class TestAdapterCleanupRetry:
    async def test_failed_response_and_server_stop_are_retried(self) -> None:
        adapter = A2AGatewayAdapter(rest_client=MagicMock())
        pending = PendingA2ATask(
            task=Task(
                id="task-123",
                context_id="ctx-123",
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            ),
            event_queue=EventQueueLegacy(),
        )
        pending.fail = AsyncMock(side_effect=[OSError("queue closed"), None])
        server = MagicMock()
        server.stop = AsyncMock(side_effect=[OSError("port still bound"), None])
        adapter._pending_tasks["room-123"] = pending
        adapter._server = server

        with pytest.raises(ExceptionGroup, match="retry host shutdown") as failed:
            await adapter.cleanup_all()

        assert [str(error) for error in failed.value.exceptions] == [
            "queue closed",
            "port still bound",
        ]
        assert adapter._pending_tasks == {"room-123": pending}
        assert adapter._server is server

        await adapter.cleanup_all()

        assert adapter._pending_tasks == {}
        assert adapter._server is None
