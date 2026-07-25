from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def runner() -> ModuleType:
    path = Path(__file__).with_name("runner.py")
    spec = importlib.util.spec_from_file_location("bug_hunting_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WaitingProcess:
    returncode = None

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return 0


class Replies:
    def assert_contains_any(self, *expected: str) -> None:
        assert expected


class Calls:
    def __len__(self) -> int:
        return 1

    def assert_fired(self, tool: str) -> None:
        assert tool == "band_store_memory"


@pytest.mark.asyncio
async def test_each_step_uses_its_own_tool_boundary(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_reads: list[dict[str, Any]] = []
    sent_at: list[datetime] = []

    class Messages:
        def snapshot(self) -> list[Any]:
            return []

    class Capture:
        messages = Messages()

        async def wait_for_processed(self, message_id: str, agent_id: str) -> None:
            return None

        async def wait_for_reply(
            self, message_id: str, agent_id: str, *, since: list[Any]
        ) -> Replies:
            return Replies()

        async def tool_calls(self, **kwargs: Any) -> Calls:
            tool_reads.append(kwargs)
            return Calls()

    capture = Capture()

    @asynccontextmanager
    async def reply_capture(*args: Any, **kwargs: Any) -> Any:
        yield capture

    import tests.e2e.baseline.toolkit.capture as capture_module

    monkeypatch.setattr(capture_module, "reply_capture", reply_capture)

    class UserOps:
        async def send_message(self, *args: Any, **kwargs: Any) -> str:
            sent_at.append(datetime.now().astimezone())
            return f"message-{len(sent_at)}"

    class Resources:
        user_ops = UserOps()

        async def provision_room(self, **kwargs: Any) -> str:
            return "room-id"

    spec = runner.ExampleSpec(
        id="example",
        path=Path("example.py"),
        config_key="agent",
        steps=(
            runner.Step("silent", barrier="processed"),
            runner.Step(
                "memory",
                tools=("band_store_memory",),
                tool_calls_at_least=1,
            ),
        ),
    )
    running = SimpleNamespace(
        spec=spec,
        agent=SimpleNamespace(id="agent-id", name="agent"),
        process=WaitingProcess(),
    )
    results: list[Any] = []

    await runner.exercise_steps(
        running,
        Resources(),
        object(),
        SimpleNamespace(e2e_timeout=1),
        "independent",
        results,
    )

    assert len(results) == 2
    assert len(tool_reads) == 1
    assert tool_reads[0]["include_memory"] is True
    assert tool_reads[0]["since"].tzinfo is not None
    assert tool_reads[0]["since"] > sent_at[0]
    assert tool_reads[0]["since"] <= sent_at[1]


@pytest.mark.asyncio
async def test_reported_step_failure_keeps_example_and_capability(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("capability failed")

    monkeypatch.setattr(runner, "exercise_steps", fail)
    running = SimpleNamespace(spec=SimpleNamespace(id="second"))
    results: list[Any] = []

    await runner.exercise_steps_reported(
        running, object(), object(), object(), "together", results
    )

    assert [result.__dict__ for result in results] == [
        {
            "scenario": "together",
            "example": "second",
            "status": "fail",
            "detail": "steps: capability failed",
        }
    ]


@pytest.mark.asyncio
async def test_group_startup_failure_is_reported_per_example(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_start(spec: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"{spec.id} failed")

    monkeypatch.setattr(runner, "start_example", fail_start)

    class Resources:
        async def provision_agent(self, label: str) -> object:
            return object()

    examples = tuple(
        runner.ExampleSpec(name, Path("example.py"), "agent")
        for name in ("first", "second")
    )
    results: list[Any] = []

    await runner.run_group(
        runner.Plan(examples, ()),
        Resources(),
        object(),
        object(),
        Path.cwd(),
        results,
    )

    assert [(result.example, result.detail) for result in results] == [
        ("first", "startup: first failed"),
        ("second", "startup: second failed"),
    ]


@pytest.mark.asyncio
async def test_process_exit_does_not_include_child_output(runner: ModuleType) -> None:
    class ExitedProcess:
        async def wait(self) -> int:
            return 7

    running = SimpleNamespace(
        spec=SimpleNamespace(id="example"),
        process=ExitedProcess(),
    )

    with pytest.raises(RuntimeError, match=r"^example exited with status 7$"):
        await runner.wait_or_exit(running, asyncio.Event().wait())
