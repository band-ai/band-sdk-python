"""Operator-owned service configuration; room input cannot choose credentials or executables."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from remote_agent.protocol import decode
from remote_agent.service import read_file
from remote_agent.types import JsonObject, SourcePath


def origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or len(value) > 2048
            or not parsed.hostname
            or any(ord(character) <= 32 or ord(character) >= 127 for character in value)
            or "?" in value
            or "#" in value
            or parsed.scheme not in ("https", "http")
            or (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "::1"))
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or parsed.port is not None
            and not 1 <= parsed.port <= 65535
        ):
            raise ValueError()
        return value.rstrip("/")
    except (TypeError, ValueError):
        raise ValueError(
            "A HTTPS Band origin or explicit loopback HTTP origin is required"
        ) from None


def private_path(value: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("Configuration and credential paths must be absolute")
    return str(Path(value).resolve())


def object_file(path: SourcePath) -> JsonObject:
    value = decode(read_file(Path(path), maximum=131072))
    if not isinstance(value, dict):
        raise ValueError("Expected a configuration object")
    return value


def credential(path: str) -> str:
    encoded = read_file(Path(private_path(path)), maximum=4097)
    token = encoded.removesuffix(b"\n")
    if not re.fullmatch(rb"[\x21-\x7e]{32,4096}", token):
        raise ValueError("Invalid credential file")
    return token.decode("ascii")


def argv(value: list[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 128
        or any(not isinstance(x, str) or not x or len(x) > 8192 for x in value)
        or not Path(value[0]).is_absolute()
        or not os.access(value[0], os.X_OK)
    ):
        raise ValueError("A fixed absolute executable argv is required")
    return value


def model_config(path: str) -> JsonObject:
    value = object_file(private_path(path))
    if (
        set(value) != {"profile", "command", "credentials"}
        or value["profile"] != "band-contract/structured-model/1"
        or not isinstance(value["credentials"], dict)
        or len(value["credentials"]) > 16
    ):
        raise ValueError("Invalid structured model configuration")
    argv(value["command"])
    for name, file in value["credentials"].items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}_(?:API_KEY|TOKEN|SECRET)", name):
            raise ValueError(
                "Only explicitly named provider credentials may enter the child environment"
            )
        credential(file)
    return value


def load_host_config(path: SourcePath) -> JsonObject:
    value = object_file(path)
    required = {
        "profile",
        "state",
        "platform_url",
        "author_ids",
        "agent_keys",
        "engine_command",
        "model_config",
        "token_file",
        "port",
    }
    optional = {
        "quality_policies",
        "auto_qualification",
        "maximum_operations",
        "assessment_timeout_seconds",
    }
    if not required <= value.keys() or value.keys() - (required | optional):
        raise ValueError("Missing or unknown program gateway configuration")
    if value["profile"] != "band-contract/program-gateway/1":
        raise ValueError("Invalid program gateway profile")
    value["platform_url"] = origin(value["platform_url"])
    for key in ("state", "model_config", "token_file"):
        value[key] = private_path(value[key])
    if not re.fullmatch(
        r"[A-Za-z0-9_~+/.=-]{32,1024}", credential(value["token_file"])
    ):
        raise ValueError("Invalid platform bearer credential")
    model_config(value["model_config"])
    argv(value["engine_command"])
    if (
        type(value["port"]) is not int
        or not 1 <= value["port"] <= 65535
        or type(value.get("auto_qualification", False)) is not bool
        or type(value.get("assessment_timeout_seconds", 150)) is not int
        or not 60 <= value.get("assessment_timeout_seconds", 150) <= 900
        or type(value.get("maximum_operations", 16)) is not int
        or not 1 <= value.get("maximum_operations", 16) <= 256
        or not isinstance(value["author_ids"], list)
        or not 1 <= len(value["author_ids"]) <= 64
        or len(set(value["author_ids"])) != len(value["author_ids"])
        or not isinstance(value["agent_keys"], dict)
        or len(value["agent_keys"]) > 256
        or not isinstance(value.get("quality_policies", []), list)
        or len(value.get("quality_policies", [])) > 64
    ):
        raise ValueError("Invalid program gateway bounds")
    try:
        if any(str(uuid.UUID(x)) != x for x in value["author_ids"]):
            raise ValueError()
        for actor, file in value["agent_keys"].items():
            if not actor.startswith("Agent:") or str(uuid.UUID(actor[6:])) != actor[6:]:
                raise ValueError()
            credential(file)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "Explicit agent identities and credential files are required"
        ) from None
    value["quality_policies"] = [
        private_path(x) for x in value.get("quality_policies", [])
    ]
    return value


def module_command(module: str, *arguments: str) -> list[str]:
    # Resolve the installed version before journaling callback identity. Old
    # runtimes keep that version across a release symlink change.
    package_root = str(Path(__file__).resolve().parents[2])
    bootstrap = (
        "import sys; sys.path.insert(0,sys.argv.pop(1)); "
        f"from remote_agent.band.{module} import main; main()"
    )
    return [sys.executable, "-I", "-B", "-c", bootstrap, package_root, *arguments]


def model_arguments(configuration: str) -> list[str]:
    path = private_path(configuration)
    fingerprint = hashlib.sha256(read_file(Path(path), maximum=131072)).hexdigest()
    return ["--model-config", path, "--model-config-sha256", fingerprint]
