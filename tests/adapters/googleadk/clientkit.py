"""Inputs for native Google client compatibility checks."""

from __future__ import annotations

from typing import Any

GEMINI_MODEL = "gemini-test"
PUBLISHER_MODEL = (
    "projects/offline/locations/us-central1/publishers/google/models/gemini-test"
)
ENDPOINT_MODEL = "projects/offline/locations/us-central1/endpoints/offline-endpoint"
TRACKING_HEADERS = {"x-goog-api-client": "offline-adk", "user-agent": "offline-agent"}
AUTH_FAILURE = "Offline credentials unavailable"


def tracking_headers(model: Any) -> dict[str, str]:
    return TRACKING_HEADERS
