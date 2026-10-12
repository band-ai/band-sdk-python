"""Runtime path-ID validation; provider schemas retain ordinary strings."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

from band.core.task_types import task_ref

ID_BODY = re.compile(r"[A-Za-z0-9_-]+")
ID_ERROR = "ID must contain only ASCII letters, digits, underscores or hyphens"


def validate_resource_id(value: str) -> str:
    """Reject values that can alter the REST route; preserve accepted text."""
    if ID_BODY.fullmatch(value) is None:
        raise ValueError(ID_ERROR)
    return value


def validate_task_reference(value: str) -> str:
    """Check the transported reference without duplicating normalization."""
    validate_resource_id(task_ref(value))
    return value


ResourceId = Annotated[str, AfterValidator(validate_resource_id)]
TaskReference = Annotated[str, AfterValidator(validate_task_reference)]
