"""Native OMP JSONL frame reassembly and host replies."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from pydantic import JsonValue, TypeAdapter

from band.integrations.omprpc.errors import OmpProtocolError
from band.integrations.ompwire import (
    OMP_CHUNK_BYTES,
    OMP_REASSEMBLED_BYTES,
    OmpChunk,
    OmpFrame,
    OmpFrameType,
    OmpUIResponse,
)

_PAYLOAD_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


@dataclass
class PendingChunks:
    chunk_id: str
    count: int
    byte_length: int
    next_index: int
    received_bytes: int
    parts: list[bytes]


class ChunkAssembler:
    """Reassemble `rpc_chunk` sequences into one logical frame."""

    def __init__(self) -> None:
        self.max_reassembled_bytes = OMP_REASSEMBLED_BYTES
        self._chunks: PendingChunks | None = None

    @property
    def pending(self) -> bool:
        return self._chunks is not None

    def reset(self) -> None:
        self._chunks = None

    def decode(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
        frame = OmpFrame.model_validate(payload)
        if frame.type != OmpFrameType.CHUNK:
            if self._chunks is not None:
                raise OmpProtocolError("OMP RPC protocol chunk sequence interrupted")
            return payload
        chunk = OmpChunk.model_validate(payload)
        if chunk.byteLength > self.max_reassembled_bytes:
            raise OmpProtocolError(
                "OMP RPC protocol frame exceeds the negotiated limit"
            )
        data = base64.b64decode(chunk.data, validate=True)
        if (
            not data
            or len(data) > OMP_CHUNK_BYTES
            or base64.b64encode(data).decode("ascii") != chunk.data
        ):
            raise OmpProtocolError("Invalid OMP RPC protocol chunk data")
        if self._chunks is None:
            if chunk.index != 0:
                raise OmpProtocolError(
                    "OMP RPC protocol chunks must start at index zero"
                )
            self._chunks = PendingChunks(
                chunk.chunkId, chunk.count, chunk.byteLength, 0, 0, []
            )
        pending = self._chunks
        if (
            pending.chunk_id != chunk.chunkId
            or pending.count != chunk.count
            or pending.byte_length != chunk.byteLength
            or pending.next_index != chunk.index
        ):
            raise OmpProtocolError("OMP RPC protocol chunk sequence mismatch")
        pending.parts.append(data)
        pending.received_bytes += len(data)
        pending.next_index += 1
        if pending.received_bytes > pending.byte_length:
            raise OmpProtocolError(
                "OMP RPC protocol chunks exceed their declared length"
            )
        if pending.next_index < pending.count:
            return None
        if pending.received_bytes != pending.byte_length:
            raise OmpProtocolError("OMP RPC protocol chunk length mismatch")
        self._chunks = None
        decoded = _PAYLOAD_ADAPTER.validate_json(b"".join(pending.parts), strict=True)
        if OmpFrame.model_validate(decoded).type == OmpFrameType.CHUNK:
            raise OmpProtocolError("OMP RPC protocol chunks cannot nest")
        return decoded


def payload_json(frame: dict[str, JsonValue]) -> str:
    return _PAYLOAD_ADAPTER.dump_json(frame).decode("utf-8")


def parse_payload_line(line: bytes) -> dict[str, JsonValue]:
    return _PAYLOAD_ADAPTER.validate_json(line, strict=True)


def ui_response_frame(request_id: str, response: OmpUIResponse) -> dict[str, JsonValue]:
    answer = response.model_dump(
        mode="json",
        exclude_none=True,
        exclude=None if response.cancelled else {"cancelled"},
    )
    return {
        "type": OmpFrameType.UI_RESPONSE,
        "id": request_id,
        **answer,
    }
