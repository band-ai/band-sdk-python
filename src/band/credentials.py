"""Public credential sentinel for the Band SDK."""

from __future__ import annotations

# Non-secret placeholder an agent passes as ``api_key`` when a trusted host-side
# proxy (e.g. a Docker Sandboxes kit) supplies the real credential on the
# outbound request. It authenticates nothing on its own; every other deployment
# must pass a genuine Band API key.
#
# The single origin of the sentinel — YAML/shell mirrors can't import it, so a
# guard test (``tests/docker/test_kit_spec.py``) fails CI if they drift. Not the
# custody-mode name ``launcher.config.CredentialSource.PROXY_MANAGED``, a
# distinct concept that happens to share this spelling.
PROXY_MANAGED_API_KEY = "proxy-managed"

__all__ = ["PROXY_MANAGED_API_KEY"]
