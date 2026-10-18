"""CLI and native RPC model discovery fixtures with observable process receipts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import cast

from pydantic import JsonValue

from tests.integrations.ompcatalogtree import start_tree_child
from tests.integrations.omppeer import Frame, Peer, send

CLI_MODELS: list[dict[str, JsonValue]] = [
    {
        "provider": "openai",
        "id": "thinker",
        "selector": "openai/thinker",
        "name": "Thinking model",
        "reasoning": True,
        "thinking": ["low", "high"],
    },
    {"provider": "local", "id": "plain", "name": "Plain model", "thinking": None},
    {"provider": "custom", "id": "unknown", "name": "Unknown efforts"},
    {"provider": "custom", "id": "none", "name": "No efforts", "thinking": []},
]
RPC_MODELS: list[dict[str, JsonValue]] = [
    {
        "provider": "openai",
        "id": "thinker",
        "name": "Thinking model",
        "api": "openai-responses",
        "baseUrl": "https://api.example.test/v1",
        "reasoning": True,
        "input": ["text"],
        "cost": {"input": 1, "output": 2, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 100000,
        "maxTokens": 10000,
        "thinking": {
            "mode": "effort",
            "efforts": ["low", "high"],
            "defaultLevel": "low",
        },
    },
    {"provider": "local", "id": "plain", "name": "Plain model", "reasoning": False},
    {
        "provider": "custom",
        "id": "unknown",
        "name": "Unknown efforts",
        "reasoning": True,
    },
]


def record(event: str, **data: JsonValue) -> None:
    with open(os.environ["OMP_CATALOG_RECEIPTS"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, "pid": os.getpid(), **data}) + "\n")


class CatalogPeer(Peer):
    def __init__(self, scenario: str) -> None:
        super().__init__(scenario)

    def handle(self, frame: Frame) -> None:
        record(
            "rpc",
            command=frame.type,
            parameters=cast(dict[str, JsonValue], frame.model_dump(exclude_none=True)),
        )
        if self.scenario == "room-recovery":
            super().handle(frame)
            if frame.type == "host_tool_result":
                if frame.isError:
                    raise AssertionError("The room reply tool failed")
                self.end("Room recovery completed")
                self.result(settled=True)
                self.settle()
            return
        match frame.type:
            case "prompt" | "new_session" | "switch_session" | "set_host_tools":
                raise AssertionError(
                    "A host catalog probe must not start a turn or room"
                )
            case "get_available_models":
                if self.scenario == "unsupported":
                    send(
                        {
                            "type": "response",
                            "id": frame.id,
                            "command": frame.type,
                            "success": False,
                            "error": "Unknown command",
                        }
                    )
                elif self.scenario == "empty-rpc":
                    self.respond(frame, {"models": []})
                elif self.scenario == "malformed-rpc":
                    self.respond(frame, {"models": [{"id": "missing-provider"}]})
                else:
                    self.respond(frame, {"models": cast(list[JsonValue], RPC_MODELS)})
            case "get_state":
                self.respond(
                    frame,
                    {
                        "sessionId": "probe-session",
                        "isSettled": True,
                        "model": {
                            "id": "thinker",
                            "provider": "openai",
                            "name": "Thinking model",
                        },
                        "thinkingLevel": "high",
                    },
                )
            case "get_available_thinking_levels":
                self.respond(frame, {"levels": ["off", "low", "high"]})
            case _:
                super().handle(frame)

    def prompt(self, frame: Frame) -> None:
        if self.scenario != "room-recovery":
            super().prompt(frame)
            return
        self.prompt_id = frame.id
        self.prompt_count += 1
        self.settled = False
        self.respond(frame)
        send({"type": "agent_start"})
        send(
            {
                "type": "host_tool_call",
                "id": "room-recovery-reply",
                "toolCallId": "room-recovery-reply",
                "toolName": "band_send_message",
                "arguments": {
                    "content": "Room reply delivered",
                    "mentions": ["requester"],
                },
            }
        )


def main() -> None:
    scenario = os.environ.get("OMP_CATALOG_SCENARIO", "cli")
    record(
        "spawn",
        arguments=list(sys.argv[1:]),
        cwd=os.getcwd(),
        credentials_match=os.environ.get("OPENAI_API_KEY") == "configured-provider-key",
        explicit_credentials_match=os.environ.get("OPENAI_API_KEY")
        == "explicit-provider-key",
    )
    if "models" in sys.argv[1:]:
        record("cli")
        if scenario == "cli":
            sys.stdout.write(json.dumps({"models": CLI_MODELS}) + "\n")
        elif scenario == "empty-cli":
            sys.stdout.write('{"models": []}\n')
        elif scenario in {"exited-cli-tree", "late-cli-tree"}:
            _child, observed = start_tree_child(
                "controller", int(os.environ["OMP_CATALOG_GATE_PORT"])
            )
            record("descendant", pid=observed.pid, created=observed.created)
            sys.stdout.write('{"models": []}\n')
        elif scenario == "invalid-cli":
            sys.stdout.write("Native RPC mode required\n")
        elif scenario == "huge-cli":
            sys.stdout.write("x" * (64 * 1024 * 1024 + 1))
            sys.stdout.flush()
            threading.Event().wait()
        elif scenario == "blocked-cli-descendant":
            child = subprocess.Popen(
                (sys.executable, "-c", "import threading; threading.Event().wait()"),
                stdin=subprocess.DEVNULL,
            )
            record("descendant", pid=child.pid)
            threading.Event().wait()
        elif scenario == "blocked-cli":
            threading.Event().wait()
        else:
            raise SystemExit(2)
        return
    if "--mode" not in sys.argv and "--mode=rpc" not in sys.argv:
        raise AssertionError("The fallback must retain the native RPC launch mode")
    peer = CatalogPeer(scenario)
    if scenario != "blocked-rpc":
        send(
            {
                "type": "ready",
                "protocolVersion": 1,
                "supportedProtocolVersions": [1, 2],
                "maxFrameBytes": 1024 * 1024,
                "maxReassembledFrameBytes": 64 * 1024 * 1024,
            }
        )
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
