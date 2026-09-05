"""Tests for shared protobuf A2A protocol helpers."""

from __future__ import annotations

from a2a.helpers import new_text_message
from a2a.types import Artifact, Part, StreamResponse, Task, TaskState, TaskStatus

from band.integrations.a2a.protocol import (
    apply_task_stream_event,
    task_id_from_stream_event,
    task_response_text,
)


def test_task_stream_updates_build_a_task_from_deltas() -> None:
    status = TaskStatus(
        state=TaskState.TASK_STATE_COMPLETED,
        message=new_text_message("Sunny"),
    )
    status_event = StreamResponse(
        status_update={
            "task_id": "task-123",
            "context_id": "context-123",
            "status": status,
        }
    )
    artifact_event = StreamResponse(
        artifact_update={
            "task_id": "task-123",
            "context_id": "context-123",
            "artifact": Artifact(
                artifact_id="artifact-123",
                parts=[Part(text="Detailed forecast")],
            ),
        }
    )

    task = apply_task_stream_event(None, status_event)
    task = apply_task_stream_event(task, artifact_event)

    assert task is not None
    assert task_id_from_stream_event(status_event) == "task-123"
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task_response_text(task) == "Detailed forecast"


def test_appended_artifact_chunks_are_combined_before_response_extraction() -> None:
    first_chunk = StreamResponse(
        artifact_update={
            "task_id": "task-123",
            "context_id": "context-123",
            "artifact": Artifact(
                artifact_id="artifact-123",
                parts=[Part(text="Part one. ")],
            ),
            "append": False,
            "last_chunk": False,
        }
    )
    final_chunk = StreamResponse(
        artifact_update={
            "task_id": "task-123",
            "context_id": "context-123",
            "artifact": Artifact(
                artifact_id="artifact-123",
                parts=[Part(text="Part two.")],
            ),
            "append": True,
            "last_chunk": True,
        }
    )

    task = apply_task_stream_event(None, first_chunk)
    task = apply_task_stream_event(task, final_chunk)

    assert task is not None
    assert len(task.artifacts) == 1
    assert task_response_text(task) == "Part one. \nPart two."


def test_artifact_overwrite_replaces_existing_artifact_content() -> None:
    first = StreamResponse(
        artifact_update={
            "task_id": "task-123",
            "context_id": "context-123",
            "artifact": Artifact(
                artifact_id="artifact-123",
                parts=[Part(text="stale content")],
            ),
            "append": False,
        }
    )
    overwrite = StreamResponse(
        artifact_update={
            "task_id": "task-123",
            "context_id": "context-123",
            "artifact": Artifact(
                artifact_id="artifact-123",
                parts=[Part(text="fresh content")],
            ),
            "append": False,
        }
    )

    task = apply_task_stream_event(None, first)
    task = apply_task_stream_event(task, overwrite)

    assert task is not None
    assert len(task.artifacts) == 1
    assert task_response_text(task) == "fresh content"


def test_task_stream_snapshot_does_not_alias_the_event() -> None:
    event = StreamResponse(
        task=Task(
            id="task-123",
            context_id="context-123",
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
    )

    task = apply_task_stream_event(None, event)
    event.task.status.state = TaskState.TASK_STATE_COMPLETED

    assert task is not None
    assert task_id_from_stream_event(event) == "task-123"
    assert task.status.state == TaskState.TASK_STATE_WORKING
