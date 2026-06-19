from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from band.integrations.codex.rpc_base import BaseJsonRpcClient

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "codex" / "codex_app_server_tool_call.jsonl"
)
_CACHED_REPLAY_FRAMES: list[dict[str, Any]] | None = None


def _clone_frame(entry: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(entry))


def _fixture_frames() -> list[dict[str, Any]]:
    global _CACHED_REPLAY_FRAMES
    if _CACHED_REPLAY_FRAMES is None:
        with _FIXTURE.open(encoding="utf-8") as fh:
            _CACHED_REPLAY_FRAMES = [json.loads(line) for line in fh if line.strip()]
    return _CACHED_REPLAY_FRAMES


def load_codex_replay_frames() -> list[dict[str, Any]]:
    return [_clone_frame(entry) for entry in _fixture_frames()]


def captured_tool_call_frame() -> dict[str, Any]:
    for entry in load_codex_replay_frames():
        frame = entry["frame"]
        if isinstance(frame, dict) and frame.get("method") == "item/tool/call":
            return frame
    raise AssertionError("fixture contains no item/tool/call frame")


def frames_with_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    call_id: str = "call-conformance",
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    replaced = False
    for cloned in load_codex_replay_frames():
        frame = cloned["frame"]
        if isinstance(frame, dict) and frame.get("method") == "item/tool/call":
            frame["params"] = {
                **dict(frame.get("params") or {}),
                "tool": tool_name,
                "arguments": dict(arguments),
                "callId": call_id,
            }
            replaced = True
        frames.append(cloned)
    if not replaced:
        raise AssertionError("fixture contains no item/tool/call frame to rewrite")
    return frames


def frames_without_tool_call() -> list[dict[str, Any]]:
    return [
        entry
        for entry in load_codex_replay_frames()
        if not (
            isinstance(entry["frame"], dict)
            and entry["frame"].get("method") == "item/tool/call"
        )
    ]


class ReplayCodexClient(BaseJsonRpcClient):
    """Replays a captured Codex wire transcript through the production parser."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        super().__init__()
        self._bootstrap_results: dict[str, dict[str, Any]] = {}
        self._server_frames: list[dict[str, Any]] = []
        self._fed = False
        self.requests_seen: list[tuple[str, dict[str, Any] | None]] = []

        out_id_to_method: dict[Any, str] = {}
        for entry in frames:
            frame = entry["frame"]
            if (
                entry["dir"] == "out"
                and isinstance(frame, dict)
                and frame.get("method")
                and frame.get("id") is not None
            ):
                out_id_to_method[frame["id"]] = str(frame["method"])

        for entry in frames:
            frame = entry["frame"]
            if entry["dir"] != "in" or not isinstance(frame, dict):
                continue
            if frame.get("method"):
                self._server_frames.append(frame)
            elif "result" in frame:
                method = out_id_to_method.get(frame.get("id"))
                if method:
                    self._bootstrap_results[method] = frame["result"]

    @property
    def thread_start_dynamic_tool_names(self) -> set[str]:
        for method, params in self.requests_seen:
            if method != "thread/start" or not isinstance(params, dict):
                continue
            return {str(tool.get("name")) for tool in params.get("dynamicTools", [])}
        return set()

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._fail_pending("replay client closed")

    async def _send_json(self, payload: dict[str, Any]) -> None:
        return None

    async def initialize(self, **_kwargs: Any) -> dict[str, Any]:
        return self._bootstrap_results.get("initialize", {})

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        del retry_on_overload
        self.requests_seen.append((method, params))
        result = self._bootstrap_results.get(method, {})
        if method == "turn/start":
            await self._feed_server_stream()
        return result

    async def _feed_server_stream(self) -> None:
        if self._fed:
            return
        self._fed = True
        for frame in self._server_frames:
            await self._dispatch_rpc_message(json.dumps(frame))
