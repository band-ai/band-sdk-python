"""Run unchanged live STOP/PLAY smoke with content-free diagnostics.

Run from the isolated SDK checkout with its source on PYTHONPATH. No retries,
extra sleeps, /next probes, orphan sweeps, or changes to the test deadline.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import platform
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

import controltrace
from controltrace import ControlTrace

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.behavior.test_control_signals import (
    test_stop_cancels_then_play_replays,
)
from tests.e2e.baseline.toolkit.capture import reply_capture
from tests.e2e.baseline.toolkit.provisioning import (
    ResourceManager,
    new_run_id,
    user_rest_client,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps
from tests.e2e.baseline.toolkit.ws import user_ws_observer
from tests.paths import REPO_ROOT


def utc_now():
    return datetime.now(UTC).isoformat()


def safe_error(error):
    # Exception messages can contain URLs with auth or arbitrary response bodies.
    return {"type": type(error).__name__}


def project_executions(payload, room_id):
    fields = {
        "id",
        "agent_id",
        "chat_room_id",
        "room_id",
        "status",
        "state",
        "stopped_at",
        "started_at",
        "completed_at",
        "inserted_at",
        "updated_at",
        "execution_id",
        "last_message_id",
    }
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return {"shape": type(rows).__name__, "executions": []}
    return {
        "executions": [
            {
                k: v
                for k, v in row.items()
                if k in fields and (v is None or isinstance(v, (str, bool, int, float)))
            }
            for row in rows
            if isinstance(row, dict)
            and row.get("chat_room_id", row.get("room_id", room_id)) == room_id
        ]
    }


def capture_state(capture):
    return {
        "room_id": capture.room_id,
        "delivery": {
            mid: {
                recipient: {"status": state.get("status")}
                for recipient, state in recipients.items()
                if isinstance(state, dict)
            }
            for mid, recipients in capture._delivery.items()
        },
        "reply_count": len(capture.messages),
    }


async def aftermath(user_ops, resources, capture, trace):
    result = {
        "utc": utc_now(),
        "capture": capture_state(capture),
        "runtime": trace.snapshot(),
    }
    result["agents"] = []
    for agent_id in resources._provisioned_agent_ids:
        item = {"agent_id": agent_id}
        try:
            response = await user_ops._control_request(
                "GET", f"/api/v1/me/agents/{agent_id}/executions"
            )
            item["http_status"] = response.status_code
            item.update(project_executions(response.json(), capture.room_id))
        except Exception as error:  # noqa: BLE001 -- record diagnostics without leaking exception content
            item["error"] = safe_error(error)
        result["agents"].append(item)
    return result


def observed_capture_factory(factory, *, user_ops, resources, trace, result):
    @asynccontextmanager
    async def observed(room_id):
        async with factory(room_id) as capture:
            try:
                yield capture
            except BaseException:
                # The test's capture context is inside the runtime context:
                # inspect before either capture teardown or cleanup PLAY.
                try:
                    result["before_cleanup"] = await aftermath(
                        user_ops, resources, capture, trace
                    )
                except Exception as error:  # noqa: BLE001 -- record diagnostics without leaking exception content
                    result["aftermath_error"] = safe_error(error)
                raise
            finally:
                result["capture"] = capture_state(capture)

    return observed


def metadata(settings):
    packages = [
        "band-sdk",
        "band-sdk-python",
        "band-sdk-core",
        "band-client-rest",
        "httpx",
        "websockets",
        "phoenix-channels-python-client",
        "pytest",
        "pytest-asyncio",
    ]
    versions = {}
    for name in packages:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    paths = [
        "src/band/runtime/execution.py",
        "src/band/runtime/runtime.py",
        "src/band/platform/link.py",
        "src/band/client/streaming/client.py",
        "tests/e2e/baseline/toolkit/control.py",
        "tests/e2e/baseline/smoke/behavior/test_control_signals.py",
    ]
    hashes = {
        p: hashlib.sha256((REPO_ROOT / p).read_bytes()).hexdigest() for p in paths
    }
    return {
        "source_root": str(REPO_ROOT),
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip(),
        "source_sha256": hashes,
        "versions": versions,
        "diagnostic_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(controltrace.__file__), Path(__file__))
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "rest_host": urlsplit(settings.endpoints.rest_url).hostname,
        "ws_host": urlsplit(settings.endpoints.ws_url).hostname,
        "deadline_s": settings.e2e_timeout,
        "autoclean": settings.run.autoclean,
    }


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


async def run_batch(args):
    settings = BaselineSettings()
    if not settings.e2e_tests_enabled or not settings.credentials.api_key_user:
        raise RuntimeError("Enable E2E_TESTS_ENABLED and configure BAND_API_KEY_USER")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = {"started_utc": utc_now(), "metadata": metadata(settings), "attempts": []}
    write_json(output / "summary.json", summary)
    client = user_rest_client(settings)
    user_ops = UserOps(client)
    run_id = new_run_id()
    failed = False
    # One observer session, like the canonical session-scoped pytest fixture.
    async with user_ws_observer(settings) as observer:
        factory = partial(
            reply_capture,
            observer,
            user_ops=user_ops,
            settings=settings,
            deadline_s=settings.e2e_timeout,
        )
        for number in range(1, args.attempts + 1):
            result = {"attempt": number, "started_utc": utc_now()}
            started = monotonic()
            resources = ResourceManager(
                user_client=client, settings=settings, run_id=run_id
            )
            with ControlTrace() as trace:
                try:
                    await test_stop_cancels_then_play_replays(
                        resource_manager=resources,
                        user_ops=user_ops,
                        reply_capture=observed_capture_factory(
                            factory,
                            user_ops=user_ops,
                            resources=resources,
                            trace=trace,
                            result=result,
                        ),
                        baseline_settings=settings,
                    )
                    result["status"] = "passed"
                except Exception as error:  # noqa: BLE001 -- record diagnostics without leaking exception content
                    result["status"] = "failed"
                    result["error"] = safe_error(error)
                    failed = True
                finally:
                    result["finished_utc"] = utc_now()
                    result["elapsed_s"] = round(monotonic() - started, 6)
                    result["agent_ids"] = list(resources._provisioned_agent_ids)
                    result["room_ids"] = list(resources._provisioned_room_ids)
                    result["after_test_runtime"] = trace.snapshot()
            trace.write(output / f"attempt-{number:03d}-trace.json")
            write_json(output / f"attempt-{number:03d}.json", result)
            # Save the evidence before deleting resources, even on cleanup failure.
            try:
                if settings.run.autoclean:
                    await resources.reap_all()
            except Exception as error:  # noqa: BLE001 -- record diagnostics without leaking exception content
                result["cleanup_error"] = safe_error(error)
                failed = True
            summary["attempts"].append(result)
            write_json(output / "summary.json", summary)
            if failed:
                break
    summary["finished_utc"] = utc_now()
    summary["status"] = "failed" if failed else "passed"
    write_json(output / "summary.json", summary)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempts", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")
    # Content-free trace replaces console SDK logs, including response/error text.
    logging.disable(logging.CRITICAL)
    return asyncio.run(run_batch(args))


if __name__ == "__main__":
    sys.exit(main())
