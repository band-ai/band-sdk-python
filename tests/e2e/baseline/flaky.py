"""Named flakiness taxonomy for the baseline suite.

Every baseline test falls into exactly one of three kinds, and the kind must be
*explicit* so a rerun policy is never chosen by guesswork:

* **deterministic** — the default. No reruns; any failure is a real bug. No marker.
* **model-nondeterministic** — a capable model occasionally fails a recall / tool /
  judge outcome it usually passes. Use :func:`flaky_model`: an ``AssertionError`` IS
  retried (that's the model having a bad moment), so the test passes if the model
  succeeds within ``reruns``. Reserve it for assertions on live model *content*;
  never use it to paper over a deterministic or infra bug.
* **infra-transient** — a live-turn timeout / cold start can fail transiently, but an
  ``AssertionError`` is a real bug. Use :func:`flaky_infra`: only non-assertion
  errors are retried; an ``AssertionError`` fails loud immediately.

Both decorators also stamp a ``flaky_reason`` marker carrying the kind and the
reason. The collection guard :func:`assert_flaky_is_classified` rejects a raw
``@pytest.mark.flaky`` (one without that stamp), so the rerun policy and the reason
are always explicit and auditable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import pytest
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

DEFAULT_RERUNS = 2

_T = TypeVar("_T")


def model_turn_retrying(*, attempts: int = DEFAULT_RERUNS + 1) -> AsyncRetrying:
    """Retry a single flaky model *turn* — the turn-level counterpart to
    :func:`flaky_model`.

    Returns a tenacity ``AsyncRetrying`` configured to retry a turn that raises
    ``AssertionError`` (a model *bad moment* — a missed recall/tool/answer it usually
    gets) up to ``attempts``, reraising the final failure with its own diagnostic. A
    non-assertion error (e.g. a ``TimeoutError`` from a stuck turn) is *not* retried —
    that is infra, not a model miss. Use tenacity's native idiom::

        async for attempt in model_turn_retrying():
            with attempt:
                mid = await user_ops.send_message(room, RECALL, mention_id=agent.id, ...)
                replies = await capture.wait_for_reply(mid, agent.id, since=mark)
                replies.assert_contains_any([marker])

    This re-drives only the flaky turn, so it is far cheaper than ``flaky_model``
    (which re-provisions and re-runs the *whole* test). ``attempts`` defaults to
    ``flaky_model``'s reruns + 1, so both give a capable model the same number of tries.
    """
    return AsyncRetrying(
        retry=retry_if_exception_type(AssertionError),
        stop=stop_after_attempt(attempts),
        reraise=True,
    )


def flaky_model(reason: str, *, reruns: int = DEFAULT_RERUNS) -> Callable[[_T], _T]:
    """Retry a test whose failure can be genuine LLM non-determinism.

    ``AssertionError`` is retried too — a capable model occasionally misses a
    recall / tool call / judged outcome it usually gets. ``reason`` documents *why*
    this test's outcome is model-dependent.
    """
    return _compose(
        pytest.mark.flaky(reruns=reruns),
        pytest.mark.flaky_reason(kind="model", reason=reason),
    )


def flaky_infra(reason: str, *, reruns: int = DEFAULT_RERUNS) -> Callable[[_T], _T]:
    """Retry only transient non-assertion failures (timeouts / cold starts).

    An ``AssertionError`` fails loud immediately — it is a real bug, not a transient.
    ``reason`` documents the transient failure mode being absorbed.
    """
    return _compose(
        pytest.mark.flaky(reruns=reruns, rerun_except=["AssertionError"]),
        pytest.mark.flaky_reason(kind="infra", reason=reason),
    )


def _compose(*marks: Any) -> Callable[[_T], _T]:
    """Apply several pytest marks as one decorator (topmost mark listed first)."""

    def apply(obj: _T) -> _T:
        for mark in reversed(marks):
            obj = mark(obj)
        return obj

    return apply


def assert_flaky_is_classified(items: list[pytest.Item]) -> None:
    """Reject a raw ``@pytest.mark.flaky`` in the baseline suite.

    Flakiness must go through :func:`flaky_model` / :func:`flaky_infra` (which stamp a
    ``flaky_reason`` marker), so every reruns policy carries an explicit kind and
    reason. A ``flaky`` marker without that stamp is a raw usage and fails collection.
    """
    offenders = sorted(
        item.nodeid
        for item in items
        if item.get_closest_marker("flaky") is not None
        and item.get_closest_marker("flaky_reason") is None
    )
    if offenders:
        listed = "\n  ".join(offenders)
        raise ValueError(
            "raw @pytest.mark.flaky is not allowed in the baseline suite — use "
            "flaky_model(reason) or flaky_infra(reason) from tests.e2e.baseline.flaky "
            "so the rerun policy and the reason are explicit. Offenders:\n  "
            f"{listed}"
        )
