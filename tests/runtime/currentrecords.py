"""Reading back the labeled current-input block a formatter hands a model."""

from __future__ import annotations

import json

from band.runtime.formatters import CURRENT_INPUTS_HEADING


def current_records(provider_input: str) -> list[dict[str, str | None]]:
    """The current-input records the model was shown, in order."""
    _, _, current = provider_input.partition(CURRENT_INPUTS_HEADING)
    return [json.loads(line) for line in current.splitlines() if line.startswith("{")]
