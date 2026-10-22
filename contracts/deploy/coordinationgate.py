"""Run the coordination checker without rewriting its tracked reference report.

The checker currently has no output option. Redirect only its known report write;
run its original assertions unchanged, preserving exceptions and process exits.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(source):
        parser.error("coordination report must be outside the source checkout")
    if output.exists():
        parser.error("coordination report output must be new")
    tracked = source / "spec/conformance/coordination-report.json"
    write_text = Path.write_text

    def write_report(path: Path, data: str, *positional: Any, **keywords: Any) -> int:
        destination = output if path.resolve() == tracked else path
        return write_text(destination, data, *positional, **keywords)

    with patch.object(Path, "write_text", write_report):
        runpy.run_module("contracts.spec.tools.checkcoordination", run_name="__main__")


if __name__ == "__main__":
    main()
