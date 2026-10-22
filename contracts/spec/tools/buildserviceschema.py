"""Generate the standalone service configuration schema. Intentional schema authoring only."""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path
from typing import Any


def obj(properties: dict[str, Any], required: Collection[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def integer(low: int, high: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": low, "maximum": high}


path = {"type": "string", "pattern": "^/", "minLength": 1}
name = {"type": "string", "minLength": 1, "maxLength": 160}
argv = {
    "type": "array",
    "minItems": 1,
    "maxItems": 64,
    "prefixItems": [path],
    "items": {"type": "string", "minLength": 1},
}
callback = obj({"command": argv, "timeout_ms": integer(1, 120_000)}, ("command",))
pricing = obj(
    {
        "input_nano_usd_per_token": integer(0, 100_000),
        "cached_input_nano_usd_per_token": integer(0, 100_000),
        "output_nano_usd_per_token": integer(0, 100_000),
        "as_of": {
            "type": "string",
            "format": "date",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
        },
    },
    (
        "input_nano_usd_per_token",
        "cached_input_nano_usd_per_token",
        "output_nano_usd_per_token",
        "as_of",
    ),
)
openai = obj(
    {
        "kind": {"const": "openai"},
        "model": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}$"},
        "timeout_ms": integer(1, 120_000),
        "max_output_tokens": integer(16, 8192),
        "max_request_bytes": integer(1024, 262_144),
        "pricing": pricing,
    },
    ("kind", "model"),
)
storage = obj(
    {
        "path": path,
        "durable": {"type": "boolean", "default": True},
        "max_records": integer(100, 10_000_000),
        "max_record_bytes": integer(4096, 4_194_304),
        "max_database_bytes": integer(1_048_576, 1_073_741_824),
    },
    ("path",),
)
engine = obj(
    {
        "command": argv,
        "contract": path,
        "format": {"enum": ["frontmatter", "standard", "file-source"]},
        "timeout_ms": integer(1, 120_000),
        "max_bytes": integer(65_536, 16_777_216),
    },
    ("command", "contract"),
)
http = obj(
    {
        "host": {"type": "string", "minLength": 1},
        "port": integer(0, 65_535),
        "tokens_file": path,
        "tls_certificate": path,
        "tls_key": path,
        "max_body_bytes": integer(1024, 1_048_576),
        "max_connections": integer(1, 128),
        "read_timeout_ms": integer(1, 120_000),
    },
    ("host", "port", "tokens_file"),
)
http["dependentRequired"] = {
    "tls_certificate": ["tls_key"],
    "tls_key": ["tls_certificate"],
}
http["allOf"] = [
    {
        "if": {"properties": {"host": {"not": {"const": "127.0.0.1"}}}},
        "then": {"required": ["tls_certificate", "tls_key"]},
    }
]
room = obj(
    {
        "session": name,
        "bindings": {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 32,
            "additionalProperties": name,
        },
        "inactivity": {"enum": ["accepted", "transition", "marked"]},
        "max_messages": integer(1, 100_000),
        "max_bytes": integer(1024, 1_048_576),
        "snapshot_every": integer(1, 1000),
        "capabilities": {
            "type": "array",
            "maxItems": 16,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "pattern": r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*/[1-9][0-9]{0,3}(?![\s\S])",
            },
        },
    },
    ("session", "bindings"),
)
remote = obj(
    {
        "url": {"type": "string", "pattern": "^https?://"},
        "token_file": path,
        "ca_file": path,
        "timeout_ms": integer(1, 120_000),
        "authentication": {"enum": ["bearer", "band-api-key"], "default": "bearer"},
    },
    ("url", "token_file"),
)
agent = obj(
    {
        "actor": name,
        "program_source": path,
        "artifact_directory": path,
        "room": remote,
        "provider": {"oneOf": [callback, openai]},
        "validator": callback,
        "control_actors": {
            "type": "object",
            "maxProperties": 256,
            "additionalProperties": {
                "type": "array",
                "items": {
                    "enum": [
                        "wake",
                        "takeover",
                        "resume",
                        "retry",
                        "repair",
                        "reconcile",
                    ]
                },
            },
        },
        "program_timeout_ms": integer(1, 30_000),
        "model_timeout_ms": integer(1, 120_000),
        "snapshot_every": integer(1, 1000),
        "max_pending": integer(1, 256),
        "max_model_calls": integer(0, 100_000),
        "max_repairs": integer(0, 1000),
        "max_attempts": integer(1, 10),
        "initial_route": {"enum": ["program", "llm"]},
    },
    ("actor", "program_source", "artifact_directory", "room", "provider"),
)
agent["allOf"] = [
    {
        "if": {
            "required": ["max_repairs"],
            "properties": {"max_repairs": {"minimum": 1}},
        },
        "then": {"required": ["validator"]},
    }
]
schema = obj(
    {
        "profile": {"const": "band-contract/service/1"},
        "kind": {"enum": ["room", "agent"]},
        "store": storage,
        "engine": engine,
        "http": http,
        "poll_interval_ms": integer(100, 60_000),
        "room": room,
        "agent": agent,
    },
    ("profile", "kind", "store", "engine", "http"),
)
schema.update(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:band-contract:service-config:1",
        "title": "Standalone room or remote agent service",
        "oneOf": [
            {
                "properties": {
                    "kind": {"const": "room"},
                    "store": {"properties": {"durable": {"const": True}}},
                },
                "required": ["room"],
                "not": {"required": ["agent"]},
            },
            {
                "properties": {"kind": {"const": "agent"}},
                "required": ["agent"],
                "not": {"required": ["room"]},
            },
        ],
    }
)
if __name__ == "__main__":
    (
        Path(__file__).resolve().parents[1] / "schemas/service-config.schema.json"
    ).write_text(json.dumps(schema, indent=2) + "\n")
