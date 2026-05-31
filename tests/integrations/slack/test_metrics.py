"""Quantitative success-metric tests for the Slack adapter.

The target: adapter overhead < 50 ms p95 added latency from "event
received" to "SDK invoked" (excluding agent runtime time). This file
pins that quantitatively so an accidentally-synchronous addition to
``_dispatch_event`` (e.g. a blocking REST call) shows up as a failure
rather than a Slack timeout in production.

What's measured: round-trip time from POST send to HTTP 200 ack. The
brain is a fast no-op stub fired into a background task; the timed
window is dominated by signature verification, JSON parse, and task
scheduling — i.e. the adapter's overhead.

Why the threshold is generous: CI runners are often 2–5× slower than a
local laptop. We assert p95 < 100 ms to leave headroom while still
being well under the 50 ms target in practice (typical observed
p95 is in the low single-digit ms on a 2024-era developer machine).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport

from thenvoi.integrations.slack.adapter import SlackAdapter
from thenvoi.integrations.slack.signature import SLACK_SIGNATURE_VERSION
from thenvoi.integrations.slack.types import SlackApp

from tests.integrations.slack.test_wrapping import (
    _SlackReplyBrain,
    _make_rest_mock,
    _mention_event,
)


# Lenient p95 bar; see module docstring. Target is 50 ms; this is
# 2× headroom for CI noise.
P95_OVERHEAD_MS_MAX = 100.0

# Sample count. ~200 keeps the test under a couple of seconds wall time
# even on slow hardware while still giving a stable p95 estimate.
SAMPLES = 200


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    base = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"{SLACK_SIGNATURE_VERSION}={digest}"


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile. Avoids a numpy dep for one number."""
    if not values:
        raise ValueError("empty sample")
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@pytest.mark.asyncio
async def test_adapter_overhead_p95_under_threshold():
    """Adapter-side latency p95 must stay well below the target bar."""
    app_config = SlackApp(
        slug="dev", bot_token="xoxb-dev", signing_secret="test-secret"
    )
    # A no-op brain so the timed window measures the adapter, not the LLM.
    inner = _SlackReplyBrain(reply=None)
    rest = _make_rest_mock([f"room-{i}" for i in range(SAMPLES + 5)])
    adapter = SlackAdapter(
        inner=inner,
        apps=[app_config],
        api_key="k",
        rest_client=rest,
        web_client_factory=lambda a: AsyncMock(
            chat_postMessage=AsyncMock(return_value={"ok": True, "ts": "x"}),
            assistant_threads_setStatus=AsyncMock(return_value={"ok": True}),
            conversations_replies=AsyncMock(return_value={"messages": []}),
        ),
    )
    adapter._thenvoi_agent_id = "bridge-uuid"  # type: ignore[attr-defined]
    await adapter.on_started("MyBot", "")

    transport = ASGITransport(app=adapter.router)
    latencies_ms: list[float] = []
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(SAMPLES):
            # Distinct channel/ts each iteration so room-creation paths
            # exercise the real branch rather than the cached fast-path.
            payload = _mention_event(
                channel=f"C{i % 8}",
                ts=f"{1700000000 + i}.000{i:03d}",
                text="<@U001> ping",
                user="U999",
            )
            body = json.dumps(payload).encode()
            timestamp = str(int(time.time()))
            signature = _sign(app_config.signing_secret, body, timestamp)

            t0 = time.perf_counter()
            response = await client.post(
                "/dev/events",
                content=body,
                headers={
                    "x-slack-request-timestamp": timestamp,
                    "x-slack-signature": signature,
                    "content-type": "application/json",
                },
            )
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            assert response.status_code == 200

    # Let background tasks drain before teardown so warnings don't pollute.
    await adapter.wait_idle()

    p50 = _percentile(latencies_ms, 0.50)
    p95 = _percentile(latencies_ms, 0.95)
    p99 = _percentile(latencies_ms, 0.99)

    # Surfaced on test failure for diagnosis; pytest shows it on -v.
    print(
        f"\nSlack adapter overhead over {SAMPLES} samples: "
        f"p50={p50:.2f} ms  p95={p95:.2f} ms  p99={p99:.2f} ms"
    )

    assert p95 < P95_OVERHEAD_MS_MAX, (
        f"Slack adapter overhead p95={p95:.2f} ms exceeded the "
        f"{P95_OVERHEAD_MS_MAX} ms safety bar (target is 50 ms). "
        "Did something synchronous land in the request path?"
    )
