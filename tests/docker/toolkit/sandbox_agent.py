"""The echo-agent workspace a Docker Sandbox runs, pointed at a Band deployment.

Used by ``examples/sandbox/self-registration/demo.py``, which provisions the
agent into the copy and network-allows the deployment's hosts.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from tests.e2e.baseline.settings import BandEndpoints
from tests.paths import KIT_DIR


def deployment_hosts(endpoints: BandEndpoints) -> list[str]:
    """The distinct hosts the agent reaches — REST and WS — in order.

    Both must be network-allowed: a deployment that serves the WS upgrade on a
    different host than REST would otherwise have its WS leg blocked while REST
    succeeds. Band uses one host for both today, so this usually collapses to one.
    """
    hosts = [urlsplit(endpoints.rest_url).hostname, urlsplit(endpoints.ws_url).hostname]
    return list(dict.fromkeys(host for host in hosts if host))


def prepare_workspace(dest: Path, *, endpoints: BandEndpoints) -> Path:
    """A throwaway copy of the echo-agent workspace pointed at ``endpoints``.

    Copying — never mounting the shipped ``echo-agent`` in place — keeps sbx's
    workspace writes out of the repo. The endpoints are non-secret and must be
    inside the VM for the launcher to resolve them (``sbx create`` takes no
    ``--env``); only the *key* stays out, injected by the proxy. Returns the
    copy's path.
    """
    workspace = dest / "echo-agent"
    shutil.copytree(KIT_DIR / "echo-agent", workspace)
    config_path = workspace / "band.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["band"] = {"restUrl": endpoints.rest_url, "wsUrl": endpoints.ws_url}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return workspace
