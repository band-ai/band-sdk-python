"""Shared path-ID examples for model and transport regressions."""

from __future__ import annotations

INVALID_IDS = (
    "",
    " ",
    "\t",
    "\n",
    " id",
    "id ",
    "id\n",
    ".",
    "..",
    "/",
    "\\",
    "?",
    "#",
    "%2F",
    "%23",
    "../memories/",
    "id?x=1",
    "id#x",
    "id\x00",
    "é",
)
UUID_ID = "ABCDEF01-2345-6789-ABCD-EF0123456789"
VALID_IDS = (UUID_ID, "fixture_1-a", "001")
TASK_REFERENCES = (*VALID_IDS, "#1", "#001", f"#{UUID_ID}")

TASK_PATHS = (
    (UUID_ID, UUID_ID),
    ("fixture_1-a", "fixture_1-a"),
    ("001", "001"),
    ("#1", "1"),
    ("#001", "001"),
    (f"#{UUID_ID}", UUID_ID),
)
