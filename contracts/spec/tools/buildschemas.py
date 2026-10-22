"""Build the reviewed portable JSON Schema bundle; no runtime source introspection."""

from __future__ import annotations

import json
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any

from contracts.spec.tools.buildserviceschema import pricing as openai_pricing
from contracts.spec.tools.specversion import REVISION

SAFE = 9_007_199_254_740_991
D: dict[str, Any] = {}


def ref(name: str) -> dict[str, Any]:
    return {"$ref": "#/$defs/" + name}


def obj(fields: dict[str, Any], optional: Collection[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": fields,
        "required": [key for key in fields if key not in optional],
        "additionalProperties": False,
    }


def array(
    item: dict[str, Any], minimum: int = 0, unique: bool = False
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": item,
        "minItems": minimum,
        **({"uniqueItems": True} if unique else {}),
    }


def mapping(item: dict[str, Any], keys: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": item,
        **({"propertyNames": keys} if keys else {}),
    }


def nullable(value: Any) -> dict[str, Any]:
    return {"anyOf": [{"type": "null"}, value]}


def enum(*values: Any) -> dict[str, Any]:
    return {"enum": list(values)}


def const(value: Any) -> dict[str, Any]:
    return {"const": value}


D["safe_integer"] = {"type": "integer", "minimum": -SAFE, "maximum": SAFE}
D["uint"] = {"type": "integer", "minimum": 0, "maximum": SAFE}
D["positive"] = {"type": "integer", "minimum": 1, "maximum": SAFE}
D["string"] = {"type": "string"}
D["identifier"] = {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"}
D["card_name"] = {
    "type": "string",
    "pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$",
    "minLength": 1,
    "maxLength": 64,
}
D["id"] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 256,
    "x-maxUtf8Bytes": 256,
    "description": "The engine also enforces at most 256 UTF-8 bytes; JSON Schema length alone counts code points.",
}
D["hash"] = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
D["json"] = {
    "anyOf": [
        {"type": "null"},
        {"type": "boolean"},
        ref("safe_integer"),
        ref("string"),
        array(ref("json")),
        mapping(ref("json")),
    ]
}
D["metadata"] = obj({"doc": nullable(ref("string")), "line": ref("positive")})
D["duration"] = {
    "oneOf": [
        const("infinite"),
        {
            "type": "array",
            "prefixItems": [const("seconds"), ref("positive")],
            "minItems": 2,
            "maxItems": 2,
        },
    ]
}
D["card"] = obj(
    {
        "name": ref("card_name"),
        "description": ref("string"),
        "engine": const("0.1.0-lab.1"),
        "roles": array(ref("card_name"), 1, True),
        "lifetime": ref("duration"),
        "inactivity_timeout": ref("duration"),
        "mentions": obj(
            {"per_message": ref("positive"), "per_session": ref("positive")}
        ),
    }
)
D["source"] = obj(
    {"card": ref("card"), "body": {"type": "string", "x-maxUtf8Bytes": 262144}}
)
D["compiler_error"] = obj(
    {
        "code": enum(
            "input",
            "source_size",
            "engine",
            "syntax",
            "documentation",
            "orphan_documentation",
            "duplicate",
            "unknown_role",
            "unknown_type",
            "unknown_state",
            "unknown_binding",
            "unmatched_reference",
            "unsatisfiable_deadline",
            "ambiguous_rule",
            "type",
            "duration",
            "rounds",
        ),
        "line": ref("positive"),
        "detail": ref("json"),
    }
)
D["field_type"] = {
    "oneOf": [
        obj({"kind": const("boolean"), "meta": ref("metadata")}),
        obj(
            {
                "kind": enum("text", "integer"),
                "min": ref("uint"),
                "max": ref("uint"),
                "meta": ref("metadata"),
            }
        ),
        obj(
            {
                "kind": enum("enum", "ref"),
                "values": array(ref("identifier"), 1, True),
                "meta": ref("metadata"),
            }
        ),
    ]
}
D["type"] = obj(
    {
        "name": ref("identifier"),
        "fields": mapping(ref("field_type"), ref("identifier")),
        "meta": ref("metadata"),
    }
)
D["rule"] = obj(
    {
        "id": {"type": "string", "pattern": "^rule-[0-9]+$"},
        "from": array(ref("card_name"), 1, True),
        "to": array(ref("card_name"), 1, True),
        "kind": ref("identifier"),
        "matches": mapping(ref("identifier"), ref("identifier")),
        "event": {"type": "boolean"},
        "each": {"type": "boolean"},
        "activity": {"type": "boolean"},
        "bind": nullable(ref("identifier")),
        "target": nullable(ref("identifier")),
        "meta": ref("metadata"),
    }
)
D["duty"] = obj(
    {
        "id": {"type": "string", "pattern": "^due-[0-9]+$"},
        "quantifier": enum("all", "any"),
        "participants": array(ref("card_name"), 1, True),
        "kind": ref("identifier"),
        "after_ms": ref("positive"),
        "outcome": ref("identifier"),
        "meta": ref("metadata"),
    }
)
D["obligation"] = obj(
    dict(
        D["duty"]["properties"],
        due_at=ref("uint"),
        remaining=array(ref("card_name"), unique=True),
    )
)
D["state_declaration"] = obj(
    {
        "name": ref("identifier"),
        "rules": array(ref("rule")),
        "deadlines": array(ref("duty")),
        "max_entries": nullable(
            obj({"limit": ref("positive"), "outcome": ref("identifier")})
        ),
        "meta": ref("metadata"),
    }
)
D["terminal"] = obj({"name": ref("identifier"), "meta": ref("metadata")})
D["role_metadata"] = obj({"name": ref("card_name"), "meta": ref("metadata")})
D["compiled"] = obj(
    {
        "name": ref("card_name"),
        "engine": const("0.1.0-lab.1"),
        "roles": array(ref("card_name"), 1, True),
        "card": ref("card"),
        "meta": ref("metadata"),
        "role_metadata": mapping(ref("role_metadata"), ref("card_name")),
        "types": mapping(ref("type"), ref("identifier")),
        "states": mapping(ref("state_declaration"), ref("identifier")),
        "terminals": mapping(ref("terminal"), ref("identifier")),
        "any_rules": array(ref("rule")),
        "room_rules": array(ref("rule")),
        "any_meta": ref("metadata"),
        "room_meta": ref("metadata"),
        "digest": ref("hash"),
        "source_digest": ref("hash"),
    }
)
D["role_definition"] = obj(
    {
        "contract": obj(
            {
                key: D["compiled"]["properties"][key]
                for key in ("name", "engine", "digest", "source_digest", "meta", "card")
            }
        ),
        "role": ref("role_metadata"),
        **{
            key: D["compiled"]["properties"][key]
            for key in (
                "types",
                "states",
                "terminals",
                "any_rules",
                "room_rules",
                "any_meta",
                "room_meta",
            )
        },
    }
)
D["scope_definition"] = obj({"rules": array(ref("rule")), "meta": ref("metadata")})
D["identity"] = obj({"definition": ref("hash"), "source": ref("hash")})
D["config"] = obj(
    {
        "session": ref("id"),
        "bindings": mapping(ref("id"), ref("card_name")),
        "now": ref("uint"),
        "inactivity": enum("accepted", "transition", "marked"),
        "max_messages": ref("positive"),
        "max_bytes": ref("positive"),
    },
    optional=("max_bytes",),
)
# Sessionless room events: the required host quota policy, plus optional per-actor shares that default to the room-wide values.
D["room_config"] = obj(
    {
        "bindings": mapping(ref("id"), ref("card_name")),
        "now": ref("uint"),
        "window_ms": ref("positive"),
        "max_events": ref("positive"),
        "max_mentions": ref("positive"),
        "max_receipts": ref("positive"),
        "max_events_per_actor": ref("positive"),
        "max_mentions_per_actor": ref("positive"),
        "max_receipts_per_actor": ref("positive"),
    },
    optional=(
        "max_events_per_actor",
        "max_mentions_per_actor",
        "max_receipts_per_actor",
    ),
)
D["envelope"] = obj(
    {
        "id": ref("id"),
        "session": ref("id"),
        "kind": ref("string"),
        "payload": mapping(ref("json")),
        "expected_version": ref("uint"),
    },
    optional=("expected_version",),
)
D["room_envelope"] = obj(
    {
        key: value
        for key, value in D["envelope"]["properties"].items()
        if key != "session"
    },
    optional=("expected_version",),
)
D["accepted"] = obj(
    {
        "status": const("accepted"),
        "id": ref("id"),
        "session": ref("id"),
        "role": ref("card_name"),
        "actor": ref("id"),
        "kind": ref("identifier"),
        "payload": mapping(ref("json")),
        "recipients": array(ref("id"), unique=True),
        "version": ref("positive"),
        "state": ref("identifier"),
        "outcome": nullable(ref("identifier")),
        "definition": ref("hash"),
        "metadata": ref("metadata"),
        # D-15: only a session started in the explicit local unchecked mode marks its decisions.
        "policy": const("unchecked"),
    },
    optional=("policy",),
)
D["accepted_redacted"] = obj(
    {
        key: value
        for key, value in D["accepted"]["properties"].items()
        if key != "payload"
    },
    optional=("policy",),
)
D["room_accepted"] = obj(
    {
        **{
            key: D["accepted"]["properties"][key]
            for key in (
                "status",
                "id",
                "role",
                "actor",
                "kind",
                "payload",
                "recipients",
                "metadata",
            )
        },
        "scope": const("room"),
    }
)
# DF-04: a payload rejection from a declared field's check names the first violation (engine.md).
D["rejection_detail"] = obj(
    {
        "field": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "x-maxUtf8Bytes": 128,
        },
        "constraint": enum(
            "required",
            "unknown",
            "type",
            "utf8",
            "bytes",
            "graphemes",
            "regex",
            "range",
            "enum",
            "ref",
        ),
        "expected": enum("object", "text", "integer", "boolean"),
        "min": ref("uint"),
        "max": ref("uint"),
    },
    optional=("field", "expected", "min", "max"),
)
D["rejected"] = obj(
    {
        "status": const("rejected"),
        "reason": {"type": "string", "minLength": 1},
        "policy": const("unchecked"),
        "detail": ref("rejection_detail"),
    },
    optional=("policy", "detail"),
)
D["decision"] = {
    "oneOf": [
        ref("accepted"),
        ref("rejected"),
        obj(
            {
                "status": const("duplicate"),
                "original": ref("accepted"),
                "policy": const("unchecked"),
            },
            optional=("policy",),
        ),
    ]
}
D["preflight_decision"] = {
    "oneOf": [
        ref("accepted"),
        ref("rejected"),
        obj({"status": const("duplicate"), "original": ref("accepted_redacted")}),
    ]
}
D["room_decision"] = {
    "oneOf": [
        ref("room_accepted"),
        ref("rejected"),
        obj({"status": const("duplicate"), "original": ref("room_accepted")}),
    ]
}
D["fact"] = obj(
    {
        "id": ref("id"),
        "kind": ref("identifier"),
        "payload": mapping(ref("json")),
        "role": ref("card_name"),
        "actor": ref("id"),
        "at": ref("uint"),
        "recipients": array(ref("id"), unique=True),
        "rule": {"type": "string", "pattern": "^rule-[0-9]+$"},
    }
)
D["fact_redacted"] = obj({"kind": ref("identifier")})
D["expiry"] = {
    "oneOf": [
        obj(
            {
                "at": ref("uint"),
                "id": ref("id"),
                "outcome": ref("identifier"),
                "reason": enum("lifetime", "inactivity"),
            }
        ),
        obj(
            {
                "at": ref("uint"),
                "id": ref("id"),
                "outcome": ref("identifier"),
                "reason": const("deadline"),
                "missing": array(ref("card_name"), 1, True),
            }
        ),
    ]
}
D["history_entry"] = {
    "oneOf": [
        obj(
            {
                "type": const("accepted"),
                "id": ref("id"),
                "at": ref("uint"),
                "role": ref("card_name"),
                "kind": ref("identifier"),
            }
        ),
        obj(
            {
                "type": const("expiry"),
                "at": ref("uint"),
                "reason": enum("lifetime", "inactivity", "deadline"),
                "outcome": ref("identifier"),
            }
        ),
    ]
}
for suffix in ("", "_redacted"):
    D["receipt" + suffix] = obj(
        {"fingerprint": ref("hash"), "decision": ref("accepted" + suffix)}
    )
    state = {
        "id": ref("id"),
        "bindings": mapping(ref("id"), ref("card_name")),
        "now": ref("uint"),
        "created_at": ref("uint"),
        "entered_at": ref("uint"),
        "last_activity": ref("uint"),
        "max_at": nullable(ref("uint")),
        "idle_ms": nullable(ref("positive")),
        "inactivity": enum("accepted", "transition", "marked"),
        "max_messages": ref("positive"),
        "max_bytes": ref("positive"),
        "mentions_used": ref("uint"),
        "state": ref("identifier"),
        "status": enum("active", "terminal"),
        "version": ref("uint"),
        "outcome": nullable(ref("identifier")),
        "expiry": nullable(ref("expiry")),
        "expired_at": nullable(ref("uint")),
        "entries": mapping(ref("positive"), ref("identifier")),
        "obligations": array(ref("obligation")),
        "contributions": mapping(array(ref("card_name"), unique=True), ref("id")),
        "variables": mapping(ref("id"), ref("identifier")),
        "facts": mapping(ref("fact" + suffix), ref("id")),
        "receipts": mapping(ref("receipt" + suffix), ref("id")),
        "history": array(ref("history_entry"))
        if not suffix
        else {"type": "array", "maxItems": 0},
        "policy_mode": const("unchecked"),
    }
    D["state" + suffix] = obj(state, optional=("policy_mode",))
    D["checkpoint_data" + suffix] = obj(
        {
            "profile": const("band-contract/state/1"),
            "definition": ref("hash"),
            "source": ref("hash"),
            "state": ref("state" + suffix),
        }
    )
D["room_checkpoint"] = obj(
    {
        "profile": const("band-contract/room-checkpoint/1"),
        "data": ref("checkpoint_data"),
        "hash": ref("hash"),
    }
)
D["view"] = obj(
    {
        "id": ref("id"),
        "state": ref("identifier"),
        "status": enum("active", "terminal"),
        "outcome": nullable(ref("identifier")),
        "version": ref("uint"),
        "definition": ref("role_definition"),
        "legal": array(ref("rule")),
        "facts": mapping(ref("fact"), ref("id")),
        "obligations": array(ref("obligation")),
        "max_at": nullable(ref("uint")),
        "idle_at": nullable(ref("uint")),
        "mentions_used": ref("uint"),
        "policy": const("unchecked"),
    },
    optional=("policy",),
)
D["projection"] = obj(
    {
        "profile": const("band-contract/agent-view/1"),
        "actor": ref("id"),
        "now": ref("uint"),
        "view": ref("view"),
        "checkpoint": ref("checkpoint_data_redacted"),
        "checkpoint_hash": ref("hash"),
    }
)
D["http_identity"] = obj(
    {
        "profile": const("contract-room/http/1"),
        "session": ref("id"),
        "identity": obj(dict(D["identity"]["properties"], ready=const(True))),
        "actor": ref("id"),
        "idempotent": const(True),
        "inbox_profile": const("contract-room/inbox/1"),
    },
    ["inbox_profile"],
)
# A negative lookahead for any character anchors the actual end, including a final newline.
uuid_pattern = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
D["band_uuid"] = {
    "type": "string",
    "pattern": "^" + uuid_pattern + r"(?![\s\S])",
    "minLength": 36,
    "maxLength": 36,
}
D["band_actor"] = {
    "type": "string",
    "pattern": "^(User|Agent):" + uuid_pattern + r"(?![\s\S])",
    "minLength": 41,
    "maxLength": 42,
}
D["band_session_config"] = obj(
    {
        "bindings": mapping(ref("band_actor"), ref("card_name")),
        "inactivity": enum("accepted", "transition", "marked"),
        "max_messages": {"type": "integer", "minimum": 1, "maximum": 10000},
        "max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536},
    }
)
D["band_create_request"] = obj(
    {
        "id": ref("band_uuid"),
        "source": {**ref("source"), "x-maxCanonicalBytes": 524288},
        "config": ref("band_session_config"),
    }
)
D["band_view_request"] = obj({})
D["band_envelope"] = obj(
    {**D["envelope"]["properties"], "session": ref("band_uuid")},
    optional=("expected_version",),
)
D["band_http_identity"] = obj(
    {
        **D["http_identity"]["properties"],
        "session": ref("band_uuid"),
        "actor": ref("band_actor"),
    }
)
D["band_wire_example"] = obj(
    {
        "id": ref("identifier"),
        "schema": enum(
            "band_create_request",
            "band_view_request",
            "band_envelope",
            "inbox_request",
            "band_http_identity",
        ),
        "valid": {"type": "boolean"},
        "value": ref("json"),
    }
)
D["band_wire_examples"] = obj(
    {
        "profile": const("band-contract/band-wire-examples/1"),
        "provenance": ref("string"),
        "examples": array(ref("band_wire_example"), 1),
    }
)
D["message"] = obj(
    {"kind": {"type": "string", "minLength": 1}, "payload": mapping(ref("json"))}
)
D["artifact"] = obj({"path": {"type": "string", "minLength": 1}, "sha256": ref("hash")})
D["coverage"] = obj(
    {
        "mode": enum("full", "partial", "llm_only"),
        "handles": array({"type": "string", "minLength": 1}),
        "delegates": array({"type": "string", "minLength": 1}),
    }
)
D["program_description"] = obj(
    {
        "schema": {"type": "string", "minLength": 1, "maxLength": 100},
        "coverage": ref("coverage"),
        "initial": mapping(ref("json")),
    }
)
D["program_result"] = {
    "oneOf": [
        obj(
            {
                "type": const("handled"),
                "state": mapping(ref("json")),
                "message": nullable(ref("message")),
                "snapshot": nullable(mapping(ref("json"))),
            }
        ),
        obj(
            {
                "type": const("delegate"),
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                "scope": enum("turn", "ongoing"),
                "repair": {"type": "boolean"},
            }
        ),
        obj({"error": {"type": "string", "maxLength": 2000}}),
    ]
}
D["isolation"] = obj(
    {
        "profile": const("remote-agent/linux-pure-python/1"),
        "memory_bytes": const(268435456),
        "cpu_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
    }
)
common_request = {
    "artifact": obj({"path": const("/program/program.py"), "sha256": ref("hash")}),
    "isolation": ref("isolation"),
    "view": mapping(ref("json")),
}
D["program_request"] = {
    "oneOf": [
        obj(dict(common_request, op=const("describe"))),
        obj(
            dict(
                common_request,
                op=const("handle"),
                event=mapping(ref("json")),
                state=mapping(ref("json")),
            )
        ),
    ]
}
D["base64"] = {
    "type": "string",
    "contentEncoding": "base64",
    "pattern": "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$",
}
D["process_result"] = obj(
    {
        "profile": const("remote-agent/process-result/1"),
        "stdout": ref("base64"),
        "stderr": ref("base64"),
        "exit_code": nullable(ref("safe_integer")),
        "failure": nullable({"type": "string", "maxLength": 2000}),
        "complete": {"type": "boolean"},
        "elapsed_ms": ref("uint"),
    }
)
D["program_input"] = obj(
    {
        "profile": const("remote-agent/program-input/1"),
        "id": {"type": "string", "minLength": 1},
        "purpose": enum("initialize", "turn", "validation"),
        "program": ref("artifact"),
        "request": ref("program_request"),
        "timeout_ms": ref("positive"),
        "max_output_bytes": ref("positive"),
    }
)
D["program_output"] = obj(
    {
        "profile": const("remote-agent/program-output/1"),
        "id": {"type": "string", "minLength": 1},
        "input_hash": ref("hash"),
        "execution": ref("process_result"),
    }
)
D["callback_result"] = obj(
    {
        "profile": const("remote-agent/callback-result/1"),
        "value": ref("json"),
        "failure": nullable({"type": "string", "maxLength": 2000}),
        "uncertain": {"type": "boolean"},
        "prefix": ref("base64"),
    },
    ["prefix"],
)
D["model_input"] = obj(
    {
        "profile": const("remote-agent/model-input/1"),
        "id": {"type": "string", "minLength": 1},
        "operation": enum("respond", "wake", "repair"),
        "provider": mapping(ref("json")),
        "request": mapping(ref("json")),
        "timeout_ms": ref("positive"),
        "max_output_bytes": ref("positive"),
    }
)
D["model_output"] = obj(
    {
        "profile": const("remote-agent/model-output/1"),
        "id": {"type": "string", "minLength": 1},
        "input_hash": ref("hash"),
        "execution": {"oneOf": [ref("process_result"), ref("callback_result")]},
    }
)
D["openai_pricing"] = openai_pricing
D["openai_provider"] = obj(
    {
        "profile": const("remote-agent/openai-provider/1"),
        "model": {"type": "string", "minLength": 2, "maxLength": 160},
        "policy_hash": ref("hash"),
        "prompt_hash": ref("hash"),
    }
)
D["openai_request"] = obj(
    {
        "profile": const("remote-agent/openai-request/1"),
        "body": obj(
            {
                "model": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}$",
                },
                "instructions": ref("string"),
                "input": ref("string"),
                "text": obj({"format": obj({"type": const("json_object")})}),
                "store": const(False),
                "stream": const(False),
                "truncation": const("disabled"),
                "service_tier": const("default"),
                "max_output_tokens": {
                    "type": "integer",
                    "minimum": 16,
                    "maximum": 8192,
                },
            }
        ),
        "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 120000},
        "max_response_bytes": const(131072),
        "pricing": nullable(ref("openai_pricing")),
    }
)
D["openai_result"] = obj(
    {
        "profile": const("remote-agent/openai-result/1"),
        "status": nullable({"type": "integer", "minimum": 100, "maximum": 599}),
        "request_id": nullable({"type": "string", "maxLength": 160}),
        "body": dict(D["base64"], maxLength=174764),
        "complete": {"type": "boolean"},
        "failure": nullable({"type": "string", "maxLength": 160}),
        "uncertain": {"type": "boolean"},
        "redacted": {"type": "boolean"},
    }
)
D["provider_usage"] = obj(
    {
        **{
            k: {"type": "integer", "minimum": 0, "maximum": 2097152}
            for k in ("input_tokens", "cached_input_tokens", "output_tokens")
        },
        "estimated_cost_micro_usd": nullable(
            {"type": "integer", "minimum": 0, "maximum": 419430400}
        ),
    }
)
D["model_usage"] = obj(
    {
        **{
            k: ref("uint")
            for k in (
                "reported_calls",
                "unreported_calls",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "costed_calls",
                "estimated_cost_micro_usd",
            )
        },
        "currency": const("USD"),
    }
)
D["control"] = obj(
    {
        "id": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,160}$"},
        "action": enum("wake", "takeover", "resume", "repair", "retry"),
        "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
    }
)
D["snapshot"] = obj(
    {
        "program": ref("hash"),
        "schema": {"type": "string", "minLength": 1},
        "state": mapping(ref("json")),
    }
)
D["journal_frame"] = obj(
    {
        "sequence": ref("positive"),
        "kind": {"type": "string", "minLength": 1, "maxLength": 100},
        "data": ref("json"),
        "updates": mapping(ref("json")),
        "previous": ref("hash"),
        "hash": ref("hash"),
    }
)
D["journal_export_header"] = obj(
    {
        "profile": const("remote-agent/journal-export/1"),
        "source_schema": const(1),
        "records": ref("uint"),
        "head": ref("hash"),
    }
)
D["recorded_result"] = obj(
    {
        "profile": const("remote-agent/recorded-result/1"),
        "kind": enum("program", "model"),
        "input": {"oneOf": [ref("program_input"), ref("model_input")]},
        "status": enum("started", "completed"),
        "execution": nullable(
            {"oneOf": [ref("process_result"), ref("callback_result")]}
        ),
        "result": ref("json"),
        "failure": nullable(ref("string")),
        "replayable": {"type": "boolean"},
        "journal_head": ref("hash"),
    }
)
D["inbox_request"] = obj(
    {"after": ref("uint"), "limit": {"type": "integer", "minimum": 1, "maximum": 128}}
)
D["inbox_head"] = obj({"sequence": ref("uint"), "hash": ref("hash")})
D["inbox_response"] = obj({"cursor": ref("uint"), "events": array(ref("accepted"))})
# K2: Band shares only cursor/events. The native standalone binding requires
# its journal identity extension without leaking it into the shared profile.
D["standalone_inbox_event"] = obj(
    {**D["accepted"]["properties"], "at": ref("uint")}, optional=("policy", "at")
)
D["standalone_inbox_response"] = obj(
    {
        "cursor": ref("uint"),
        "events": array(ref("standalone_inbox_event")),
        "head": ref("inbox_head"),
    }
)
D["invocation_fixture"] = obj(
    {
        "profile": const("band-contract/invocation-fixture/1"),
        "input": ref("program_input"),
        "output": ref("program_output"),
        "journal": array(ref("journal_frame"), 2),
        "recovered_result": ref("program_result"),
        "interrupted_result": const("hold"),
    }
)
profiles = [
    "compiler/1",
    "session-engine/1",
    "room-events/1",
    "durable-room/1",
    "remote-agent/1",
    "standalone-http/1",
    "band-room/1",
]
D["requirement"] = obj(
    {
        "id": {"type": "string", "pattern": "^[A-Z]+-[0-9]{3}$"},
        "level": enum("MUST", "SHOULD"),
        "ledger": {"type": "string", "pattern": "^P[1-9][0-9]*$"},
        "profiles": array(enum(*profiles), 1, True),
        "specification": {"type": "string", "pattern": "^[a-z-]+\\.md$"},
        "statement": {"type": "string", "minLength": 10},
        "acceptance": array({"type": "string", "minLength": 10}, 1),
    }
)
D["requirements"] = obj(
    {
        "profile": const("band-contract/requirements/1"),
        "revision": const(REVISION),
        "production_ledger": const("../PRODUCTION.md"),
        "requirements": array(ref("requirement"), 1),
    }
)
D["coverage_counts"] = obj(
    {
        key: ref("uint")
        for key in (
            "requirements",
            "requirements_with_examples",
            "requirements_without_examples",
        )
    }
)
D["coverage_requirement"] = obj(
    {
        **{
            key: D["requirement"]["properties"][key]
            for key in ("id", "profiles", "specification", "acceptance")
        },
        "examples": obj(
            {
                "cases": array(ref("string"), unique=True),
                "artifacts": array(ref("string"), unique=True),
            }
        ),
    }
)
D["coverage_index"] = obj(
    {
        "profile": const("band-contract/coverage/1"),
        "revision": const(REVISION),
        "qualification": const("not_assessed"),
        "summary": ref("coverage_counts"),
        "profiles": mapping(ref("coverage_counts"), enum(*profiles)),
        "requirements": array(ref("coverage_requirement"), 1),
    }
)
root = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://band.ai/spec/contract/protocol/1",
    "title": f"Band contract portable protocol schemas, revision {REVISION}",
    "description": "Select a named $defs entry. Semantic cross-field, byte-length and grapheme constraints are additionally specified in the normative documents.",
    "$defs": D,
}
if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "schemas" / "protocol.schema.json"
    path.write_text(json.dumps(root, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.write(str(f"{len(D)} definitions written") + "\n")
