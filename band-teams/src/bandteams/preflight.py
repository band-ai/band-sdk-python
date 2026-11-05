"""Refuse deployment until the operator supplies the qualification inputs."""

from __future__ import annotations

import json
import time
from importlib.metadata import version
from pathlib import Path

from bandteams.settings import BandSettings, Manifest, TeamsSettings

DEPENDENCIES = {
    "microsoft-teams-apps": "2.1.1",
    "band-client-rest": "0.0.50",
    "phoenix-channels-python-client": "0.4.4",
}


def dependencies() -> dict[str, str]:
    installed = {name: version(name) for name in DEPENDENCIES}
    if installed != DEPENDENCIES:
        raise ValueError("Installed dependencies differ from the qualified pins")
    return installed


def preflight(
    manifest: Manifest, teams: TeamsSettings, band: BandSettings, state: Path
) -> None:
    dependencies()
    if any(person.tenant_id != teams.TENANT_ID for person in manifest.people):
        raise ValueError("BLOCKED: manifest and app tenant differ")
    if not teams.CLIENT_SECRET.get_secret_value():
        raise ValueError("BLOCKED: missing Microsoft client secret")
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise ValueError("BLOCKED: state requires a private local directory")
    if manifest.deployment == "local-tunnel" and (
        manifest.tunnel_expires_at is None or manifest.tunnel_expires_at <= time.time()
    ):
        raise ValueError("BLOCKED: tunnel validity is missing or expired")
    if not manifest.posts_layout_verified or not all(
        person.private_owner_verified and person.rsc_consent_verified
        for person in manifest.people
    ):
        raise ValueError(
            "BLOCKED: operator privacy, layout and consent checks incomplete"
        )
    if (
        not manifest.entitlement_evidence.is_file()
        or not manifest.entitlement_evidence.stat().st_size
    ):
        raise ValueError("BLOCKED: independent Band admission evidence missing")
    package = json.loads(manifest.installed_manifest.read_text(encoding="utf-8"))
    bots = package.get("bots", [])
    if not any(
        bot.get("botId") == teams.CLIENT_ID
        and {"personal", "team"} <= set(bot.get("scopes", []))
        for bot in bots
    ):
        raise ValueError("BLOCKED: installed bot identity/scopes do not match")
    permissions = (
        package.get("authorization", {})
        .get("permissions", {})
        .get("resourceSpecific", [])
    )
    if not any(
        item.get("name") == "ChannelMessage.Read.Group"
        and item.get("type") == "Application"
        for item in permissions
    ):
        raise ValueError("BLOCKED: installed manifest lacks channel RSC")
