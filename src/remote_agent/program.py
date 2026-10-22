"""Pinned Python artifacts and bounded callback execution."""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Self

from remote_agent.commands import (
    CommandError,
    capture,
    failure_capture,
    result_from_capture,
)
from remote_agent.environment import minimal_environment
from remote_agent.publication import publish_file, recover_staging
from remote_agent.sandbox import SandboxError, command, policy
from remote_agent.storage import StoreError, canonical, private_directory, private_file
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.invocations import InvocationJournal


class ProgramError(Exception):
    pass


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str

    @classmethod
    def publish(cls, source: str, directory: SourcePath) -> Self:
        encoded = source.encode("utf-8")
        if len(encoded) > 262_144:
            raise ProgramError("Program source capacity exceeded")
        checksum = hashlib.sha256(encoded).hexdigest()
        try:
            directory = private_directory(directory)
        except StoreError as error:
            raise ProgramError(str(error)) from error
        path = directory / (checksum + ".py")
        try:
            publish_file(path, lambda target: target.write(encoded), exist_ok=True)
            recover_staging(path)
            with os.fdopen(private_file(path, create=False), "rb") as existing:
                observed = existing.read(262_145)
            if observed != encoded:
                raise ProgramError("Existing artifact hash mismatch")
        except StoreError as error:
            raise ProgramError(str(error)) from error
        return cls(str(path), checksum)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def verify(self) -> None:
        try:
            with os.fdopen(private_file(self.path, create=False), "rb") as source:
                encoded = source.read(262_145)
        except (OSError, StoreError) as error:
            raise ProgramError("Cannot verify the pinned program artifact") from error
        if len(encoded) > 262_144 or hashlib.sha256(encoded).hexdigest() != self.sha256:
            raise ProgramError("Program source hash mismatch")


class Program:
    def __init__(
        self,
        artifact: Artifact,
        *,
        timeout: float = 2,
        journal: InvocationJournal | None = None,
    ) -> None:
        self.artifact, self.timeout = artifact, timeout
        self.journal = journal

    def request(self, op: str, **payload: Any) -> JsonObject:
        return dict(
            payload,
            op=op,
            artifact={"path": "/program/program.py", "sha256": self.artifact.sha256},
            isolation=policy(self.timeout),
        )

    def execute(self, request: JsonObject, *, max_bytes: int = 262_144) -> JsonObject:
        try:
            argv, enforced = command(self.artifact, timeout=self.timeout)
            if request["isolation"] != enforced:
                raise SandboxError(
                    "Recorded execution policy differs from the installed boundary"
                )
            return capture(
                argv,
                request,
                timeout=self.timeout,
                max_bytes=max_bytes,
                env=minimal_environment(path="/usr/bin:/bin"),
            )
        except (SandboxError, OSError) as error:
            return failure_capture(error)

    @staticmethod
    def result(execution: JsonObject, *, op: str | None = "handle") -> JsonObject:
        try:
            result = result_from_capture(execution)
        except CommandError as error:
            raise ProgramError(str(error)) from error
        if "error" in result:
            raise ProgramError(result["error"])
        # Generated code owns stdout and can exit before the worker validates it.
        # Enforce both callback ABIs in the host, including for durable replay.
        if op == "describe":
            if set(result) != {"schema", "coverage", "initial"}:
                raise ProgramError("Invalid describe reply fields")
            schema, coverage = result["schema"], result["coverage"]
            if not isinstance(schema, str) or not schema or len(schema) > 100:
                raise ProgramError("Invalid state schema version")
            if (
                not isinstance(coverage, dict)
                or set(coverage) != {"mode", "handles", "delegates"}
                or coverage["mode"] not in ("full", "partial", "llm_only")
                or any(
                    not isinstance(coverage[key], list)
                    or any(not isinstance(x, str) or not x for x in coverage[key])
                    for key in ("handles", "delegates")
                )
                or coverage["mode"] != "full"
                and not coverage["delegates"]
            ):
                raise ProgramError(
                    "Declare full, partial, or llm_only coverage and delegation conditions"
                )
            if not isinstance(result["initial"], dict):
                raise ProgramError("Initial state must be an object")
            return result
        if op != "handle":
            raise ProgramError("Unknown callback")
        if result.get("type") == "delegate":
            if set(result) != {"type", "reason", "scope", "repair"}:
                raise ProgramError("Invalid delegation reply fields")
            if (
                not isinstance(result.get("reason"), str)
                or not result["reason"]
                or len(result["reason"]) > 2000
                or result.get("scope") not in ("turn", "ongoing")
                or type(result.get("repair")) is not bool
            ):
                raise ProgramError("Invalid delegation request")
        elif result.get("type") == "handled":
            if set(result) - {"type", "state", "message", "snapshot"}:
                raise ProgramError("Invalid handled reply fields")
            if not isinstance(result.get("state"), dict) or "message" not in result:
                raise ProgramError("Invalid reply state or message")
            message = result["message"]
            if message is not None and (
                not isinstance(message, dict)
                or set(message) != {"kind", "payload"}
                or not isinstance(message["kind"], str)
                or not message["kind"]
                or not isinstance(message["payload"], dict)
            ):
                raise ProgramError("Invalid program message")
            if result.get("snapshot") is not None and canonical(
                result["snapshot"]
            ) != canonical(result["state"]):
                raise ProgramError("Staged snapshot differs from the completed state")
        else:
            raise ProgramError("Expected Handled or Delegate")
        return result

    def _run(
        self,
        op: str,
        *,
        invocation_id: str | None = None,
        purpose: str = "validation",
        **payload: Any,
    ) -> JsonObject:
        request = self.request(op, **payload)
        execution = (
            self.journal.run(
                self, request, invocation_id=invocation_id, purpose=purpose
            )
            if self.journal
            else self.execute(request)
        )
        return self.result(execution, op=op)

    def describe(
        self,
        view: JsonObject,
        *,
        invocation_id: str | None = None,
        purpose: str = "validation",
    ) -> JsonObject:
        return self._run(
            "describe", view=view, invocation_id=invocation_id, purpose=purpose
        )

    def handle(
        self,
        event: JsonObject,
        state: JsonObject,
        view: JsonObject,
        *,
        invocation_id: str | None = None,
        purpose: str = "validation",
    ) -> JsonObject:
        return self._run(
            "handle",
            event=event,
            state=state,
            view=view,
            invocation_id=invocation_id,
            purpose=purpose,
        )
