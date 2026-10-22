"""Scripted provider for the demo; replace this command with an authorized LLM provider."""

from __future__ import annotations

import json
import pathlib
import sys

bundle = json.loads(pathlib.Path(sys.argv[1]).read_text())
source = pathlib.Path(__file__).with_name("counter_template.ex.txt").read_text()
pathlib.Path(sys.argv[2]).write_text(
    source.replace("CANDIDATE_MODULE", bundle["candidate_module"])
)
