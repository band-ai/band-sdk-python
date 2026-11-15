"""Released CrewAI failure-message contract for tests without the optional extra."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolFailure(BaseModel):
    """Preserve failure identity and the 1.15.30 model-facing text contract."""

    model_config = ConfigDict(frozen=True)
    message: str
    code: str | None = None

    def as_agent_message(self) -> str:
        return f"{self.message} (code: {self.code})" if self.code else self.message


def tool_result_data(result: Any) -> Any:
    """Decode the model-facing success or native failure envelope."""
    return json.loads(result if isinstance(result, str) else result.as_agent_message())
