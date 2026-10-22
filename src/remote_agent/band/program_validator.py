"""Independent host-owned repair validator; proposed source cannot change this policy."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from remote_agent.band.arithmetic import validate_candidate
from remote_agent.output import write_line
from remote_agent.program import Artifact
from remote_agent.service import read_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--role")
    parser.add_argument("--contract-sha256")
    args = parser.parse_args()
    value = json.load(sys.stdin)
    artifact = Artifact(**value["candidate"])
    artifact.verify()
    options = {}
    if args.qualification or args.role or args.contract_sha256:
        if not (args.qualification and args.role and args.contract_sha256):
            raise ValueError("quality_configuration_incomplete")
        options = {
            "qualification": json.loads(read_file(args.qualification, maximum=524288)),
            "role": args.role,
            "contract_sha256": args.contract_sha256,
        }
    with tempfile.TemporaryDirectory(prefix="quality-") as directory:
        result = validate_candidate(
            Path(artifact.path).read_text(), Path(directory), **options
        )
    write_line(json.dumps({"ok": result["ok"], "checks": result["checks"]}))


if __name__ == "__main__":
    main()
