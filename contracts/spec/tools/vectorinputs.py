"""Portable source descriptors and trusted host evidence for conformance inputs."""

from __future__ import annotations

import hashlib
from typing import Any

from contracts.spec.tools.paths import CONTRACTS_ROOT as ROOT


def file_source(content: str, **fields: Any) -> dict[str, Any]:
    return {
        "profile": "band-contract/file-source/1",
        "format": "frontmatter",
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        **fields,
    }


def source(name: str) -> dict[str, Any]:
    return file_source(
        (ROOT / "spec/conformance/sources-v2" / f"{name}.band").read_text()
    )


POLICY_REVISION = "conformance-1"

MEMBER_OPERATIONS = ("step", "wire_step", "view", "stop_contribution")


def trusted_policy(bindings: dict[str, Any]) -> dict[str, Any]:
    actors = [value for value in bindings.values() if isinstance(value, str)]
    actors += [
        member["actor"]
        for value in bindings.values()
        if isinstance(value, list)
        for member in value
        if isinstance(member, dict) and isinstance(member.get("actor"), str)
    ]
    return {
        "revision": POLICY_REVISION,
        "fixed_roles": {},
        "principals": {
            actor: {"kind": "agent", "active": True, "permission": "owner"}
            for actor in actors
        },
    }


def hosted(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach host evidence: a policy at `new`, then the session's policy on each member operation.
    A request that already names a room_policy (or a new config that does) is sent unchanged."""

    def field(value: Any, *path: str | None) -> Any:
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        return value

    policies, result = {}, []
    for request in requests:
        op, handle = request.get("op"), request.get("handle")
        if op == "new":
            bindings = field(request, "config", "bindings")
            if (
                isinstance(bindings, dict)
                and field(request, "config", "room_policy") is None
            ):
                request = {
                    **request,
                    "config": {
                        **request["config"],
                        "room_policy": trusted_policy(bindings),
                    },
                }
            policies[handle] = field(request, "config", "room_policy")
        elif op == "restore":
            bindings = field(request, "checkpoint", "data", "state", "bindings")
            policies[handle] = (
                trusted_policy(bindings) if isinstance(bindings, dict) else None
            )
        elif (
            op in MEMBER_OPERATIONS
            and "room_policy" not in request
            and field(policies, handle, "principals")
        ):
            # An unchecked local session needs no evidence on later calls, so none is sent.
            request = {**request, "room_policy": policies[handle]}
        result.append(request)
    return result
