from __future__ import annotations

import json
from typing import Any

from tests.paths import CONTRACT_RUNTIME_TESTS

LUCAS_PROMPT_CASES = json.loads(
    (CONTRACT_RUNTIME_TESTS / "fixtures" / "ubf_prompt_cases.json").read_text()
)["cases"]


def case(suffix: Any) -> Any:
    return next(c for c in LUCAS_PROMPT_CASES if c["id"].endswith(suffix))
