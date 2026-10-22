"""Provider-neutral command integration. Commands are trusted host configuration."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from remote_agent.commands import CommandError, call, capture, result_from_capture
from remote_agent.environment import minimal_environment
from remote_agent.program import Artifact, ProgramError
from remote_agent.storage import StoreError, digest
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.program import Program


class ProviderError(Exception):
    pass


class CommandProvider:
    def __init__(
        self, argv: list[str], artifact_directory: SourcePath, *, timeout: float = 30
    ) -> None:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(x, str) or not x for x in argv)
        ):
            raise ProviderError(
                "Configure a fixed executable argv; shell commands are not accepted"
            )
        self.argv, self.timeout = list(argv), timeout
        self.directory = Path(artifact_directory).resolve()
        self.prompt = Path(__file__).with_name("sidecar_prompt.md").read_text()
        self.identity = {
            "profile": "remote-agent/command-model/1",
            "argv_hash": digest(argv),
            "prompt_hash": hashlib.sha256(self.prompt.encode()).hexdigest(),
        }

    def prepare(self, operation: str, bundle: JsonObject) -> JsonObject:
        try:
            artifact = bundle["program"]
            path = Path(artifact["path"]).resolve()
            if not path.is_relative_to(self.directory):
                raise ProviderError("Program source is outside the artifact directory")
            with path.open("rb") as source:
                encoded = source.read(262_145)
            if (
                len(encoded) > 262_144
                or hashlib.sha256(encoded).hexdigest() != artifact["sha256"]
            ):
                raise ProviderError("Program source hash mismatch")
            return {
                "operation": operation,
                "system_prompt": self.prompt,
                "program_source": encoded.decode("utf-8"),
                "bundle": bundle,
            }
        except (CommandError, StoreError, OSError, ValueError) as error:
            raise ProviderError(str(error)) from error

    def _environment(self) -> dict[str, str]:
        return minimal_environment(pythonpath=Path(__file__).resolve().parent.parent)

    def execute(self, request: JsonObject, *, timeout: float) -> JsonObject:
        return capture(
            self.argv,
            request,
            timeout=min(self.timeout, timeout),
            max_bytes=262_144,
            max_input_bytes=524_288,
            env=self._environment(),
        )

    def decode(self, operation: str, execution: JsonObject) -> JsonObject:
        try:
            result = result_from_capture(execution)
            if "error" in result:
                raise ProviderError(str(result["error"])[:2000])
            return self._repair_result(result) if operation == "repair" else result
        except (CommandError, StoreError, OSError, ValueError) as error:
            raise ProviderError(str(error)) from error

    def _request(self, operation: str, bundle: JsonObject) -> JsonObject:
        request = self.prepare(operation, bundle)
        return self.decode(operation, self.execute(request, timeout=self.timeout))

    def respond(self, bundle: JsonObject) -> JsonObject:
        return self._request("respond", bundle)

    def wake(self, bundle: JsonObject) -> JsonObject:
        return self._request("wake", bundle)

    def repair(self, bundle: JsonObject) -> JsonObject:
        return self._request("repair", bundle)

    def _repair_result(self, result: JsonObject) -> JsonObject:
        if (
            set(result) != {"source", "resume"}
            or not isinstance(result["source"], str)
            or type(result["resume"]) is not bool
        ):
            raise ProviderError(
                "Repair must return Python source and a boolean resume policy"
            )
        try:
            artifact = Artifact.publish(result["source"], self.directory)
        except (ProgramError, OSError) as error:
            raise ProviderError(str(error)) from error
        return {"artifact": artifact.to_dict(), "resume": result["resume"]}


class CommandValidator:
    def __init__(self, argv: list[str], *, timeout: float = 30) -> None:
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(x, str) or not x for x in argv)
        ):
            raise ProviderError("A host-controlled validator argv is required")
        self.argv, self.timeout = list(argv), timeout

    def __call__(self, candidate: Program, bundle: JsonObject) -> JsonObject:
        try:
            return call(
                self.argv,
                {
                    "operation": "validate",
                    "candidate": candidate.artifact.to_dict(),
                    "bundle": bundle,
                },
                timeout=self.timeout,
                env=minimal_environment(
                    pythonpath=Path(__file__).resolve().parent.parent
                ),
            )
        except (CommandError, StoreError, OSError) as error:
            raise ProviderError(str(error)) from error
