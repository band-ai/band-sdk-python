"""Durable program I/O. Live executions never resample; pure failed probes may retry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from remote_agent.program import Program, ProgramError
from remote_agent.storage import StoreError, canonical, digest
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.store import Store


class InvocationUnresolved(Exception):
    pass


class InvocationJournal:
    MAX_BYTES = 262_144
    MAX_PROBE_ATTEMPTS = 8
    MAX_PROBE_TIMEOUTS = 16

    @staticmethod
    def _successful_probe(execution: JsonObject, request: JsonObject) -> bool:
        try:
            Program.result(execution, op=request.get("op"))
        except (ProgramError, StoreError, KeyError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _transient_probe(execution: JsonObject) -> bool:
        # Only a host-observed timeout is transient, not worker JSON mentioning
        # a timeout or an incomplete/unknown result after a host crash.
        return execution.get("complete") is False and execution.get("failure") in (
            "Callback timeout",
            "timeout",
        )

    def __init__(self, store: Store) -> None:
        self.store = store

    def run(
        self,
        program: Program,
        request: JsonObject,
        *,
        invocation_id: str | None = None,
        purpose: str = "validation",
    ) -> JsonObject:
        data = {
            "profile": "remote-agent/program-input/1",
            "purpose": purpose,
            "program": program.artifact.to_dict(),
            "request": request,
            "timeout_ms": round(program.timeout * 1000),
            "max_output_bytes": self.MAX_BYTES,
        }
        # Pure validation reuses only a successful durable result. Retain every
        # failed/interrupted attempt under its original ID and try a fresh suffix.
        # Explicit live-turn and initialization IDs keep their fail-closed rules.
        probe = invocation_id is None
        base_id = invocation_id if not probe else "probe:" + digest(data)
        attempt, failures, timeouts = 0, 0, 0
        with self.store.lock:
            while True:
                if probe and failures >= self.MAX_PROBE_ATTEMPTS:
                    raise InvocationUnresolved(
                        "Program probe retry limit exhausted; change the candidate or probe input"
                    )
                if probe and timeouts >= self.MAX_PROBE_TIMEOUTS:
                    raise InvocationUnresolved(
                        "Program probe timeout limit exhausted; change the candidate or probe input"
                    )
                invocation_id = (
                    base_id if attempt == 0 else base_id + ":retry:" + str(attempt)
                )
                recorded_input = dict(data, id=invocation_id)
                if len(canonical(recorded_input)) > self.MAX_BYTES:
                    raise InvocationUnresolved(
                        "Program input exceeds the journaled execution limit"
                    )
                fingerprint, key = digest(recorded_input), "invocation:" + invocation_id
                prior = self.store.get(key)
                if not prior:
                    break
                if prior["fingerprint"] != fingerprint:
                    raise InvocationUnresolved(
                        "Invocation identity conflicts with its recorded input"
                    )
                if probe:
                    execution = prior.get("execution", {})
                    if prior["status"] == "completed" and self._successful_probe(
                        execution, request
                    ):
                        return execution
                    if prior["status"] == "completed" and self._transient_probe(
                        execution
                    ):
                        timeouts += 1
                    else:
                        failures += 1
                    attempt += 1
                    continue
                if prior["status"] != "completed":
                    raise InvocationUnresolved(
                        "Program invocation has no durable result; reconcile before retrying"
                    )
                return prior["execution"]
            self.store.commit(
                "program_input",
                recorded_input,
                updates={key: {"fingerprint": fingerprint, "status": "started"}},
            )
        execution = program.execute(request, max_bytes=self.MAX_BYTES)
        output = {
            "profile": "remote-agent/program-output/1",
            "id": invocation_id,
            "input_hash": fingerprint,
            "execution": execution,
        }
        self.store.commit(
            "program_output",
            output,
            updates={
                key: {
                    "fingerprint": fingerprint,
                    "status": "completed",
                    "execution": execution,
                }
            },
        )
        return execution

    def recorded(self, invocation_id: str) -> JsonObject:
        """Read a verified invocation without running a program or publishing an effect."""
        inputs = [
            r["data"]
            for r in self.store.records()
            if r["kind"] == "program_input" and r["data"]["id"] == invocation_id
        ]
        if len(inputs) != 1:
            raise InvocationUnresolved("Unknown or ambiguous invocation")
        prior = self.store.get("invocation:" + invocation_id)
        if not prior or prior["fingerprint"] != digest(inputs[0]):
            raise InvocationUnresolved("Invocation input and index disagree")
        return {
            "input": inputs[0],
            "status": prior["status"],
            "execution": prior.get("execution"),
        }
