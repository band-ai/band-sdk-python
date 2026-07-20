"""Public credential sentinel for the Band SDK."""

from __future__ import annotations

# Non-secret placeholder an agent may pass as its ``api_key`` when a trusted
# host-side proxy replaces the request credential before it reaches Band (e.g.
# a Docker Sandboxes kit injecting the real key on the outbound request). It is
# not a credential and authenticates nothing on its own — only the proxy that
# supplies the real key makes the request authenticated. Every other deployment
# must pass a genuine Band API key.
#
# This is the importable, documented value for Python callers; it does not
# dedupe the kit's own declarations (its ``spec.yaml`` / ``sbx secret
# --placeholder``, which are YAML/shell and can't import it), which must carry
# the same string by agreement.
PROXY_MANAGED_API_KEY = "proxy-managed"

__all__ = ["PROXY_MANAGED_API_KEY"]
