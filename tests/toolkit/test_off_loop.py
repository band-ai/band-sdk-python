"""``off_loop`` exits exactly what it entered, even when its caller is cancelled."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from tests.toolkit.tasks import off_loop

RESOURCE = "resource"


@contextmanager
def recording(
    exits: list[BaseException | None],
    *,
    suppress: bool = False,
    entering: threading.Event | None = None,
    release: threading.Event | None = None,
    enter_error: Exception | None = None,
) -> Iterator[str]:
    """Record each exit's error; optionally hold the enter until ``release``."""
    if entering is not None and release is not None:
        entering.set()
        release.wait()
    if enter_error is not None:
        raise enter_error
    try:
        yield RESOURCE
    except Exception as error:
        exits.append(error)
        if not suppress:
            raise
    else:
        exits.append(None)


class TestOffLoop:
    @pytest.mark.parametrize(
        ("body_error", "suppress", "propagates"),
        [
            pytest.param(None, False, False, id="clean-exit"),
            pytest.param(RuntimeError("body"), False, True, id="error-propagates"),
            pytest.param(RuntimeError("body"), True, False, id="exit-suppresses"),
        ],
    )
    async def test_exit_receives_the_body_outcome(
        self, body_error: Exception | None, suppress: bool, propagates: bool
    ) -> None:
        exits: list[BaseException | None] = []

        async def use() -> None:
            async with off_loop(recording(exits, suppress=suppress)) as value:
                assert value == RESOURCE
                if body_error is not None:
                    raise body_error

        if propagates:
            with pytest.raises(RuntimeError):
                await use()
        else:
            await use()

        assert exits == [body_error]

    @pytest.mark.parametrize(
        ("enter_error", "expected_exits"),
        [
            pytest.param(None, [None], id="entered-is-exited"),
            pytest.param(RuntimeError("enter"), [], id="failed-enter-not-exited"),
        ],
    )
    async def test_cancel_during_enter_exits_what_the_thread_entered(
        self,
        enter_error: Exception | None,
        expected_exits: list[None],
    ) -> None:
        exits: list[BaseException | None] = []
        entering, release = threading.Event(), threading.Event()
        manager = recording(
            exits, entering=entering, release=release, enter_error=enter_error
        )

        async def use() -> None:
            async with off_loop(manager):
                pytest.fail("a cancelled enter must not run the body")

        task = asyncio.create_task(use())
        await asyncio.to_thread(entering.wait)
        task.cancel()
        await asyncio.sleep(0)
        returned_before_enter = task.done()
        # Open the gate before asserting, so a failure never strands the thread.
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert not returned_before_enter, "the cancelled caller must wait for the enter"
        assert exits == expected_exits
