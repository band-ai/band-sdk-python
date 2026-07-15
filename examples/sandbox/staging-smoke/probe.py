"""
Band-platform driver for the Docker Sandbox staging smoke.

Unlike `agent.py` (which must run as a standalone example against just the
published `band-sdk` package inside the sandbox), this script runs on the
operator's host from within this repository's own dev environment
(`uv sync --extra dev`) and reuses `tests/e2e/baseline`'s pytest-free toolkit
directly — `ResourceManager` (dynamic agent/room provisioning + reap +
orphan sweep), `UserOps.send_message`, and `reply_capture`/`wait_for_reply` —
instead of hand-rolling REST/WS calls or requiring a static pre-provisioned
staging agent. It has no PEP 723 header (unlike every sibling script in this
directory except `agent.py`) because `tests/e2e/baseline` is dev-only source
in this repo, not part of the published package a PEP 723 isolated venv could
install; it needs the surrounding project's own synced environment. It is an
internal repo tool, not a redistributable customer example, even though it
lives under `examples/` for discovery alongside the runbook and skill.

One entry point, six labels, because they all share the same toolkit
construction (settings, REST client):

    uv run probe.py --label provision              # run.sh: mint agent+room
    uv run probe.py --label initial                # first round trip
    uv run probe.py --label after-wifi-reconnect    # after the Wi-Fi cycle
    uv run probe.py --label after-sleep-wake        # after host sleep/wake
    uv run probe.py --label after-daemon-restart    # after the daemon bounce
    uv run probe.py --label cleanup                # final teardown

`provision` prints exactly four `KEY=value` lines to stdout — the fresh
agent's `BAND_AGENT_ID`/`BAND_API_KEY` plus the *validated* `BAND_WS_URL`/
`BAND_REST_URL` (the same values `load_settings()` guarded, alias-resolved
from `.env.test`) — for `run.sh` to capture and inject into the sandboxed
process's environment. Nothing else goes to stdout, and no credential is
written to `.sandbox-smoke/state.json` (see `state.py`). Emitting the URLs
here keeps run.sh's agent on exactly the endpoints the production guard
checked, instead of whatever the raw shell environment happens to hold.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

import state

# Reuse tests/e2e/baseline's toolkit directly (see module docstring) — insert
# the repo root (state.repo_root() is fixed by this example's own location in
# the tree, not a hardcoded parents[N] hop count re-derived here) so
# `tests.e2e.baseline.*` imports the same way `conftest.py` does for pytest.
sys.path.insert(0, str(state.repo_root()))

from band_rest import AsyncRestClient  # noqa: E402

from tests.e2e.baseline.settings import BandEndpoints, BaselineSettings  # noqa: E402
from tests.e2e.baseline.toolkit.capture import reply_capture  # noqa: E402
from tests.e2e.baseline.toolkit.provisioning import (  # noqa: E402
    ResourceManager,
    user_rest_client,
)
from tests.e2e.baseline.toolkit.user_ops import UserOps  # noqa: E402
from tests.e2e.baseline.toolkit.ws import user_ws_observer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,  # `provision` uses stdout as its KEY=value protocol
)
logger = logging.getLogger(__name__)


def load_settings() -> BaselineSettings:
    """Load Band config, rejecting the SDK's own production defaults outright.

    Reads the *declared* defaults straight off `BandEndpoints`'s field
    metadata rather than comparing against a second, hand-typed copy of the
    URLs (which could silently drift from the real default) — and rather
    than constructing a fresh `BandEndpoints()` (which would just read the
    same environment `settings.endpoints` did, making the comparison
    vacuous).
    """
    settings = BaselineSettings()
    production_rest_url = BandEndpoints.model_fields["rest_url"].default
    production_ws_url = BandEndpoints.model_fields["ws_url"].default
    if settings.endpoints.rest_url == production_rest_url:
        raise ValueError(
            "BAND_REST_URL is unset or points at production; set it to the "
            "staging REST URL before running this smoke."
        )
    if settings.endpoints.ws_url == production_ws_url:
        raise ValueError(
            "BAND_WS_URL is unset or points at production; set it to the "
            "staging WebSocket URL before running this smoke."
        )
    if not settings.credentials.api_key_user:
        raise ValueError("BAND_API_KEY_USER is required (the staging user key)")
    return settings


def bootstrap() -> tuple[BaselineSettings, AsyncRestClient]:
    """Settings + a user-authenticated REST client — the one piece every
    label below needs, built once instead of three separate times."""
    settings = load_settings()
    return settings, user_rest_client(settings)


async def _reap_recorded_resources(
    manager: ResourceManager, run_state: state.SmokeState
) -> bool:
    """Reap the room/agent ids recorded in ``run_state``; True if all reaped.

    Each resource is reaped independently: a failed room delete must never
    leave the (live, credentialed) agent running because its reap was skipped.
    An id is cleared from the state only after *its* reap succeeds — a failed
    reap keeps the id recorded, so the resource stays retryable instead of
    surviving only as a log line (rooms especially: the orphan sweep covers
    aged agents by name prefix, but nothing ever sweeps rooms). Used both when
    re-provisioning over a dead process (daemon-restart recovery, a crashed
    launch) and by terminal cleanup.
    """
    all_reaped = True
    for id_field, reap, kind in (
        ("room_id", manager.reap_room, "room"),
        ("agent_id", manager.reap_agent, "agent"),
    ):
        resource_id = getattr(run_state, id_field)
        if not resource_id:
            continue
        try:
            await reap(resource_id)
        except Exception:
            all_reaped = False
            logger.exception(
                "Failed to reap %s %s — id kept in state for a retry",
                kind,
                resource_id,
            )
            continue
        logger.info("Reaped %s %s", kind, resource_id)
        setattr(run_state, id_field, "")
    if not run_state.agent_id:
        run_state.agent_name = ""
    return all_reaped


async def provision(sandbox_name: str, sbx_version: str, sdk_version: str) -> None:
    settings, client = bootstrap()
    run_state = state.begin_provision()
    manager = ResourceManager(
        user_client=client, settings=settings, run_id=run_state.run_id
    )

    # Reap leftovers this workflow itself may have abandoned (a crashed prior
    # run's agent is invisible to any later run once its state file rotates) —
    # the toolkit's sweep is prefix- and age-guarded, so nothing else's
    # resources are ever touched.
    await manager.sweep_orphans()
    # Re-provisioning within a run (daemon-restart recovery, a crashed
    # launch): the recorded agent/room belong to a dead process — replace
    # them. Refuse to stack new resources on top of ones that failed to reap
    # (their ids stay recorded, so a rerun retries the reap first).
    if not await _reap_recorded_resources(manager, run_state):
        state.save(run_state)
        logger.error(
            "Could not reap this run's previous resources; rerun to retry "
            "before provisioning replacements."
        )
        raise SystemExit(1)

    # Each id is persisted to state.json the moment its resource exists,
    # BEFORE the next fallible step — so any failure (or even a SIGKILL)
    # leaves a retryable record on disk, never a resource whose id survives
    # only in a log line. The participant-add is deliberately a separate step
    # after the room id is recorded, not provision_room's participants=
    # argument, which would create the room and then raise past us on a
    # failed add with the room id still unrecorded.
    run_state.sandbox_name = sandbox_name
    run_state.sbx_version = sbx_version
    run_state.sdk_version = sdk_version
    try:
        agent = await manager.provision_agent("sandbox")
        run_state.agent_id = agent.id
        run_state.agent_name = agent.name
        state.save(run_state)
        # A descriptive title, not the platform's default untitled-room
        # label, so an operator watching the Band UI can find this run's
        # room among others at a glance.
        room_id = await manager.provision_room(
            title=f"Sandbox smoke — {run_state.run_id}"
        )
        run_state.room_id = room_id
        state.save(run_state)
        await UserOps(client).add_participant(room_id, agent.id)
    except Exception:
        logger.exception(
            "Provisioning failed partway; reaping what was created so far "
            "so nothing is left running on staging"
        )
        if not await _reap_recorded_resources(manager, run_state):
            logger.error(
                "Some resources could not be reaped — their ids remain in "
                "state.json; rerun `probe.py --label cleanup` to retry."
            )
        state.save(run_state)
        raise

    logger.info("Provisioned agent %s and room %s", agent.id, room_id)
    # The only stdout output: run.sh captures these four lines verbatim.
    # The URLs are the validated settings values, so the sandboxed agent can
    # only ever target the endpoints the production guard checked.
    sys.stdout.write(f"BAND_AGENT_ID={agent.id}\n")
    sys.stdout.write(f"BAND_API_KEY={agent.api_key}\n")
    sys.stdout.write(f"BAND_WS_URL={settings.endpoints.ws_url}\n")
    sys.stdout.write(f"BAND_REST_URL={settings.endpoints.rest_url}\n")


async def _run_probe(label: str) -> None:
    settings, client = bootstrap()
    run_state = state.load()
    user_ops = UserOps(client)
    marker = uuid.uuid4().hex[:12]

    async with (
        user_ws_observer(settings) as tracking,
        reply_capture(
            tracking,
            run_state.room_id,
            user_ops=user_ops,
            settings=settings,
            deadline_s=settings.e2e_timeout,
        ) as capture,
    ):
        logger.info("Sending marker %s (label=%s)", marker, label)
        mid = await user_ops.send_message(
            run_state.room_id,
            f"marker:{marker}",
            mention_id=run_state.agent_id,
            mention_name=run_state.agent_name,
        )
        try:
            replies = await capture.wait_for_reply(mid, run_state.agent_id)
            replies.assert_contains_any([f"sandbox-ack:{marker}"])
        except (TimeoutError, AssertionError) as error:
            run_state.probes.append(
                state.ProbeResult(
                    label=label, marker=marker, passed=False, detail=str(error)
                )
            )
            state.save(run_state)
            logger.error("Probe %s failed: %s", label, error)
            raise SystemExit(1) from error

    run_state.probes.append(state.ProbeResult(label=label, marker=marker, passed=True))
    state.save(run_state)
    logger.info("Probe %s passed (marker=%s)", label, marker)


async def cleanup() -> None:
    """Terminal teardown: reap the run's room + agent and end the run.

    This is the one place `probe.py` writes `phase` — the run's shared
    terminal transition, so the plain operator workflow (which never calls
    `record-phase.py`) still produces a finished run that the next
    `provision` rotates instead of resuming. Mid-run resource replacement
    (daemon-restart recovery) is handled by `provision` itself, never by
    calling this.
    """
    settings, client = bootstrap()
    run_state = state.load()
    manager = ResourceManager(
        user_client=client, settings=settings, run_id=run_state.run_id
    )
    if not await _reap_recorded_resources(manager, run_state):
        # Don't end the run over live resources: the surviving ids stay in
        # state.json, so rerunning `--label cleanup` retries exactly them.
        state.save(run_state)
        logger.error("Cleanup incomplete — rerun `probe.py --label cleanup` to retry.")
        raise SystemExit(1)
    run_state.phase = "completed"
    state.save(run_state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        required=True,
        # The three "after-*" labels are the recovery probes, one per
        # interruption scenario — each proves the same marker/reply round
        # trip after its interruption, rather than an operator eyeballing
        # the log.
        choices=[
            "provision",
            "initial",
            "after-wifi-reconnect",
            "after-sleep-wake",
            "after-daemon-restart",
            "cleanup",
        ],
    )
    parser.add_argument("--sandbox-name", default="")
    parser.add_argument("--sbx-version", default="")
    parser.add_argument("--sdk-version", default="")
    args = parser.parse_args()

    match args.label:
        case "provision":
            asyncio.run(
                provision(args.sandbox_name, args.sbx_version, args.sdk_version)
            )
        case "cleanup":
            asyncio.run(cleanup())
        case label:
            asyncio.run(_run_probe(label))


if __name__ == "__main__":
    main()
