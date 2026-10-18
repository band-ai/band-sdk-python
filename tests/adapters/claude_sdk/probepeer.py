"""A real stream-json handshake peer that rejects inference requests."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from enum import StrEnum
from pathlib import Path


class ProbeMode(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    EOF = "eof"
    HOLD = "hold"


class ProbeEnv(StrEnum):
    MODE = "BAND_CLAUDE_PROBE_MODE"
    RECEIPT = "BAND_CLAUDE_PROBE_RECEIPT"
    INFO = "BAND_CLAUDE_PROBE_INFO"
    MARKER = "BAND_CLAUDE_PROBE_MARKER"


PRIVATE_ERROR = "provider-secret https://private.provider.invalid/api"


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--setting-sources")
    options, _ = parser.parse_known_args()
    mode = ProbeMode(os.environ[ProbeEnv.MODE])
    receipt = Path(os.environ[ProbeEnv.RECEIPT])
    for line in sys.stdin:
        message = json.loads(line)
        with receipt.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "message": message,
                        "cwd": str(Path.cwd()),
                        "marker": os.environ.get(ProbeEnv.MARKER),
                        "argv": sys.argv[1:],
                        "setting_sources": options.setting_sources.split(",")
                        if options.setting_sources
                        else [],
                    }
                )
                + "\n"
            )
        if message["type"] == "user":
            raise RuntimeError("A standalone handshake attempted inference")
        if message["type"] != "control_request":
            continue
        if mode == ProbeMode.EOF:
            return
        if mode == ProbeMode.HOLD:
            threading.Event().wait()
        response = (
            {"subtype": "error", "error": PRIVATE_ERROR}
            if mode == ProbeMode.ERROR
            else {
                "subtype": "success",
                "response": json.loads(os.environ[ProbeEnv.INFO]),
            }
        )
        print(
            json.dumps(
                {
                    "type": "control_response",
                    "response": {"request_id": message["request_id"], **response},
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
