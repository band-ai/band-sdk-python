from __future__ import annotations

from typing import Any

from remote_agent.modeljournal import ModelUnresolved
from tests.contracts.runtime import hybrid


class OffSchemaOnce(hybrid.ScriptedModel):
    """The first reply is parseable JSON with an invalid scope; later replies are valid."""

    def respond(self, bundle: Any) -> Any:
        reply = super().respond(bundle)
        return (
            dict(reply, scope="conversation")
            if self.calls.count("respond") == 1
            else reply
        )


class AlwaysOffSchema(hybrid.ScriptedModel):
    def respond(self, bundle: Any) -> dict[str, Any]:
        return dict(super().respond(bundle), state="not-an-object")


class UncertainOnce(hybrid.ScriptedModel):
    """A prepared provider whose first completed record decodes as uncertain, like an
    OpenAI 408/5xx after dispatch: a complete capture with no definitive result."""

    def prepare(self, operation: Any, bundle: Any) -> dict[str, Any]:
        return {"operation": operation, "bundle": bundle}

    def execute(self, request: Any, timeout: Any) -> dict[str, Any]:
        operation = request["operation"]
        if operation == "respond" and self.calls.count("respond") == 0:
            self.calls.append("respond")
            value = {"uncertain": True}
        else:
            value = getattr(self, operation)(request["bundle"])
        return {
            "profile": "remote-agent/callback-result/1",
            "value": value,
            "failure": None,
            "uncertain": False,
        }

    def decode(self, operation: Any, execution: Any) -> Any:
        if execution["value"].get("uncertain"):
            raise ModelUnresolved("Provider reported no definitive result")
        return execution["value"]


class ModelWedgeFixture(hybrid.HybridFixture):
    def start_provider(self, provider: Any) -> Any:
        self.provider = provider
        return hybrid.HybridFixture.start(
            self, control_actors={"operator": ["retry", "takeover", "resume"]}
        )

    def finished(self, reason: Any) -> list[Any]:
        return [
            r["data"]
            for r in self.store.records()
            if r["kind"] == "execution_finished" and r["data"]["reason"] == reason
        ]
