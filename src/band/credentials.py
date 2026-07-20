"""Public credential sentinel for the Band SDK."""

from __future__ import annotations

# Non-secret placeholder an agent may pass as its ``api_key`` when a trusted
# host-side proxy replaces the request credential before it reaches Band (e.g.
# a Docker Sandboxes kit injecting the real key on the outbound request). It is
# not a credential and authenticates nothing on its own — only the proxy that
# supplies the real key makes the request authenticated. Every other deployment
# must pass a genuine Band API key.
#
# This module is the single origin of the sentinel for Python callers. The
# YAML/shell mirrors (the kit ``sbx secret --placeholder`` docs) can't import it,
# so a guard test (``tests/docker/test_kit_spec.py``) asserts they equal this
# value — drift fails CI rather than diverging silently.
#
# Distinct from ``band.docker.launcher.config.CredentialSource.PROXY_MANAGED``,
# the custody-*mode* name a ``band.yaml`` selects (``credentials.source``): same
# spelling, different concept, kept as separate constants on purpose.
PROXY_MANAGED_API_KEY = "proxy-managed"

__all__ = ["PROXY_MANAGED_API_KEY"]
