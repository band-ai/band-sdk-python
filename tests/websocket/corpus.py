"""
Generated from band-sdk-core's event-validation corpus -- do not hand-edit.

Source: band-ai/band-sdk-core, tag band-sdk-core-py-v0.7.1, commit
0b6b21240b8daf3284df2ec44de28cfa7beaa59c
(crates/core/tests/fixtures/corpus/*.json5). Regenerate by re-vendoring those
files and re-running the conversion that produced this module: parse each
with json5, keep each case's name/raw/canonical, drop the sdk.python/
sdk.typescript contrast columns (informational only, not asserted on).
"""

from __future__ import annotations

from typing import Any

CORPUS_CASES: list[tuple[str, dict[str, Any]]] = [
    (
        "agent.control",
        {
            "name": "agent_scope_interrupt",
            "raw": {
                "type": "agent.control",
                "mode": "interrupt",
                "scope": "agent",
                "agent_id": "agent-ac01",
                "execution_id": "exec-ac01",
                "room_id": None,
                "reason": "user_requested",
                "correlation_id": "ctl-AbCdEfGh==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "type": "agent.control",
                    "mode": "interrupt",
                    "scope": "agent",
                    "agent_id": "agent-ac01",
                    "execution_id": "exec-ac01",
                    "room_id": None,
                    "reason": "user_requested",
                    "correlation_id": "ctl-AbCdEfGh==",
                },
            },
        },
    ),
    (
        "agent.control",
        {
            "name": "room_scope_stop",
            "raw": {
                "type": "agent.control",
                "mode": "stop",
                "scope": "room",
                "agent_id": "agent-ac02",
                "execution_id": "exec-ac02",
                "room_id": "room-ac02",
                "reason": "user_requested",
                "correlation_id": "ctl-IjKlMnOp==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "type": "agent.control",
                    "mode": "stop",
                    "scope": "room",
                    "agent_id": "agent-ac02",
                    "execution_id": "exec-ac02",
                    "room_id": "room-ac02",
                    "reason": "user_requested",
                    "correlation_id": "ctl-IjKlMnOp==",
                },
            },
        },
    ),
    (
        "agent.control",
        {
            "name": "play_mode_custom_reason",
            "raw": {
                "type": "agent.control",
                "mode": "play",
                "scope": "agent",
                "agent_id": "agent-ac03",
                "execution_id": None,
                "room_id": None,
                "reason": "automatic_resume",
                "correlation_id": "ctl-QrStUvWx==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "type": "agent.control",
                    "mode": "play",
                    "scope": "agent",
                    "agent_id": "agent-ac03",
                    "execution_id": None,
                    "room_id": None,
                    "reason": "automatic_resume",
                    "correlation_id": "ctl-QrStUvWx==",
                },
            },
        },
    ),
    (
        "agent.control",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "type": "agent.control",
                "mode": "interrupt",
                "scope": "agent",
                "agent_id": "agent-ac04",
                "execution_id": "exec-ac04",
                "room_id": None,
                "reason": "user_requested",
                "correlation_id": "ctl-YzAbCdEf==",
                "x_future_field": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "type": "agent.control",
                    "mode": "interrupt",
                    "scope": "agent",
                    "agent_id": "agent-ac04",
                    "execution_id": "exec-ac04",
                    "room_id": None,
                    "reason": "user_requested",
                    "correlation_id": "ctl-YzAbCdEf==",
                    "x_future_field": "keep-me",
                },
            },
        },
    ),
    (
        "agent.control",
        {
            "name": "invalid_mode",
            "raw": {
                "type": "agent.control",
                "mode": "pause",
                "scope": "agent",
                "agent_id": "agent-ac05",
                "execution_id": None,
                "room_id": None,
                "reason": "user_requested",
                "correlation_id": "ctl-InVal1dMode==",
            },
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "mode",
                        "code": "invalid_value",
                        "message": "`mode` must be one of: interrupt, stop, play",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "contact_added",
        {
            "name": "full_valid_agent_contact",
            "raw": {
                "id": "contact-01",
                "handle": "acme/sage",
                "name": "Sage",
                "type": "Agent",
                "inserted_at": "2026-07-20T08:00:00Z",
                "description": "Research assistant",
                "is_external": True,
                "listed_in_directory": True,
                "tags": ["research", "internal"],
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "contact-01",
                    "handle": "acme/sage",
                    "name": "Sage",
                    "type": "Agent",
                    "inserted_at": "2026-07-20T08:00:00Z",
                    "description": "Research assistant",
                    "is_external": True,
                    "is_remote": True,
                    "listed_in_directory": True,
                    "tags": ["research", "internal"],
                },
            },
        },
    ),
    (
        "contact_added",
        {
            "name": "handle_and_name_explicit_null",
            "raw": {
                "id": "contact-02",
                "handle": None,
                "name": None,
                "type": "User",
                "inserted_at": "2026-07-21T08:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "contact-02",
                    "handle": None,
                    "name": None,
                    "type": "User",
                    "inserted_at": "2026-07-21T08:00:00Z",
                },
            },
        },
    ),
    (
        "contact_added",
        {
            "name": "minimal_no_optional_fields",
            "raw": {
                "id": "contact-03",
                "handle": "acme/nova",
                "name": "Nova",
                "type": "User",
                "inserted_at": "2026-07-22T08:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "contact-03",
                    "handle": "acme/nova",
                    "name": "Nova",
                    "type": "User",
                    "inserted_at": "2026-07-22T08:00:00Z",
                },
            },
        },
    ),
    (
        "contact_added",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "id": "contact-04",
                "handle": "acme/echo",
                "name": "Echo",
                "type": "Agent",
                "inserted_at": "2026-07-23T08:00:00Z",
                "x_custom": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "contact-04",
                    "handle": "acme/echo",
                    "name": "Echo",
                    "type": "Agent",
                    "inserted_at": "2026-07-23T08:00:00Z",
                    "x_custom": "keep-me",
                },
            },
        },
    ),
    (
        "contact_removed",
        {
            "name": "valid",
            "raw": {"id": "contact-20"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "contact-20"},
            },
        },
    ),
    (
        "contact_removed",
        {
            "name": "unknown_passthrough_field",
            "raw": {"id": "contact-21", "x_custom": "keep-me"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "contact-21", "x_custom": "keep-me"},
            },
        },
    ),
    (
        "contact_request_received",
        {
            "name": "full_valid_with_handle_and_name",
            "raw": {
                "id": "creq-01",
                "from_handle": "acme/scout",
                "from_name": "Scout",
                "message": "Let's connect",
                "status": "pending",
                "inserted_at": "2026-07-10T08:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "creq-01",
                    "from_handle": "acme/scout",
                    "from_name": "Scout",
                    "message": "Let's connect",
                    "status": "pending",
                    "inserted_at": "2026-07-10T08:00:00Z",
                },
            },
        },
    ),
    (
        "contact_request_received",
        {
            "name": "from_handle_and_from_name_absent",
            "raw": {
                "id": "creq-02",
                "message": "Let's connect",
                "status": "pending",
                "inserted_at": "2026-07-11T08:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "creq-02",
                    "message": "Let's connect",
                    "status": "pending",
                    "inserted_at": "2026-07-11T08:00:00Z",
                },
            },
        },
    ),
    (
        "contact_request_received",
        {
            "name": "message_absent",
            "raw": {
                "id": "creq-03",
                "from_handle": "acme/nova",
                "from_name": "Nova",
                "status": "pending",
                "inserted_at": "2026-07-12T08:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "creq-03",
                    "from_handle": "acme/nova",
                    "from_name": "Nova",
                    "status": "pending",
                    "inserted_at": "2026-07-12T08:00:00Z",
                },
            },
        },
    ),
    (
        "contact_request_received",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "id": "creq-04",
                "from_handle": "acme/echo",
                "from_name": "Echo",
                "status": "pending",
                "inserted_at": "2026-07-13T08:00:00Z",
                "x_custom": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "creq-04",
                    "from_handle": "acme/echo",
                    "from_name": "Echo",
                    "status": "pending",
                    "inserted_at": "2026-07-13T08:00:00Z",
                    "x_custom": "keep-me",
                },
            },
        },
    ),
    (
        "contact_request_updated",
        {
            "name": "valid",
            "raw": {"id": "creq-10", "status": "approved"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "creq-10", "status": "approved"},
            },
        },
    ),
    (
        "contact_request_updated",
        {
            "name": "unknown_passthrough_field",
            "raw": {"id": "creq-11", "status": "rejected", "x_custom": "keep-me"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "creq-11",
                    "status": "rejected",
                    "x_custom": "keep-me",
                },
            },
        },
    ),
    (
        "contact_request_updated",
        {
            "name": "missing_status",
            "raw": {"id": "creq-12"},
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "status",
                        "code": "missing",
                        "message": "required field `status` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "message_created",
        {
            "name": "full_valid_with_mentions_and_attachment",
            "raw": {
                "id": "msg-0001",
                "content": "hello @sage, please check this",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-aaa1",
                "sender_name": "Ari Rivera",
                "metadata": {
                    "mentions": [{"id": "agent-bbb2", "name": "sage", "handle": "sage"}]
                },
                "attachments": [
                    {
                        "id": "file-cc03",
                        "name": "notes.txt",
                        "bytes": 128,
                        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                        "content_type": "text/plain",
                        "has_thumb": False,
                        "expires_at": None,
                    }
                ],
                "inserted_at": "2026-08-01T10:00:00Z",
                "updated_at": "2026-08-01T10:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "msg-0001",
                    "content": "hello @sage, please check this",
                    "message_type": "text",
                    "sender_type": "User",
                    "sender_id": "user-aaa1",
                    "sender_name": "Ari Rivera",
                    "metadata": {
                        "mentions": [
                            {"id": "agent-bbb2", "name": "sage", "handle": "sage"}
                        ]
                    },
                    "attachments": [
                        {
                            "id": "file-cc03",
                            "name": "notes.txt",
                            "bytes": 128,
                            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                            "content_type": "text/plain",
                            "has_thumb": False,
                            "expires_at": None,
                        }
                    ],
                    "inserted_at": "2026-08-01T10:00:00Z",
                    "updated_at": "2026-08-01T10:00:00Z",
                },
            },
        },
    ),
    (
        "message_created",
        {
            "name": "metadata_mentions_absent",
            "raw": {
                "id": "msg-0002",
                "content": "no mentions here",
                "message_type": "text",
                "sender_type": "Agent",
                "sender_id": "agent-dd04",
                "metadata": {},
                "attachments": [],
                "inserted_at": "2026-08-01T10:05:00Z",
                "updated_at": "2026-08-01T10:05:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "msg-0002",
                    "content": "no mentions here",
                    "message_type": "text",
                    "sender_type": "Agent",
                    "sender_id": "agent-dd04",
                    "metadata": {"mentions": []},
                    "attachments": [],
                    "inserted_at": "2026-08-01T10:05:00Z",
                    "updated_at": "2026-08-01T10:05:00Z",
                },
            },
        },
    ),
    (
        "message_created",
        {
            "name": "metadata_mentions_explicit_null",
            "raw": {
                "id": "msg-0003",
                "content": "explicit null mentions",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-ee05",
                "metadata": {"mentions": None},
                "attachments": [],
                "inserted_at": "2026-08-01T10:06:00Z",
                "updated_at": "2026-08-01T10:06:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "msg-0003",
                    "content": "explicit null mentions",
                    "message_type": "text",
                    "sender_type": "User",
                    "sender_id": "user-ee05",
                    "metadata": {"mentions": []},
                    "attachments": [],
                    "inserted_at": "2026-08-01T10:06:00Z",
                    "updated_at": "2026-08-01T10:06:00Z",
                },
            },
        },
    ),
    (
        "message_created",
        {
            "name": "unknown_passthrough_and_phantom_fields",
            "raw": {
                "id": "msg-0004",
                "content": "carries fields no SDK models",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-ff06",
                "sender_name": None,
                "chat_room_id": "room-gg07",
                "thread_id": "thread-hh08",
                "metadata": {
                    "mentions": [],
                    "status": "delivered",
                    "x_custom": "keep-me",
                },
                "attachments": [],
                "inserted_at": "2026-08-01T10:07:00Z",
                "updated_at": "2026-08-01T10:07:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "msg-0004",
                    "content": "carries fields no SDK models",
                    "message_type": "text",
                    "sender_type": "User",
                    "sender_id": "user-ff06",
                    "sender_name": None,
                    "chat_room_id": "room-gg07",
                    "thread_id": "thread-hh08",
                    "metadata": {
                        "mentions": [],
                        "status": "delivered",
                        "x_custom": "keep-me",
                    },
                    "attachments": [],
                    "inserted_at": "2026-08-01T10:07:00Z",
                    "updated_at": "2026-08-01T10:07:00Z",
                },
            },
        },
    ),
    (
        "message_created",
        {
            "name": "mention_item_not_an_object",
            "raw": {
                "id": "msg-0005",
                "content": "malformed mention",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-jj10",
                "metadata": {"mentions": [1]},
                "attachments": [],
                "inserted_at": "2026-08-01T10:09:00Z",
                "updated_at": "2026-08-01T10:09:00Z",
            },
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "metadata.mentions[0]",
                        "code": "wrong_type",
                        "message": "`metadata.mentions[0]` must be an object",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "message_created",
        {
            "name": "mention_item_missing_id",
            "raw": {
                "id": "msg-0006",
                "content": "mention with no id",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-kk11",
                "metadata": {"mentions": [{"handle": "sage"}]},
                "attachments": [],
                "inserted_at": "2026-08-01T10:10:00Z",
                "updated_at": "2026-08-01T10:10:00Z",
            },
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "metadata.mentions[0].id",
                        "code": "missing",
                        "message": "required field `metadata.mentions[0].id` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "message_created",
        {
            "name": "missing_required_field",
            "raw": {
                "content": "no id supplied",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-ii09",
                "metadata": {},
                "attachments": [],
                "inserted_at": "2026-08-01T10:08:00Z",
                "updated_at": "2026-08-01T10:08:00Z",
            },
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "id",
                        "code": "missing",
                        "message": "required field `id` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "message_updated",
        {
            "name": "shares_message_created_shape",
            "raw": {
                "id": "msg-0010",
                "content": "edited content",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-jj10",
                "metadata": {"mentions": []},
                "attachments": [],
                "inserted_at": "2026-08-01T11:00:00Z",
                "updated_at": "2026-08-01T11:05:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "msg-0010",
                    "content": "edited content",
                    "message_type": "text",
                    "sender_type": "User",
                    "sender_id": "user-jj10",
                    "metadata": {"mentions": []},
                    "attachments": [],
                    "inserted_at": "2026-08-01T11:00:00Z",
                    "updated_at": "2026-08-01T11:05:00Z",
                },
            },
        },
    ),
    (
        "message_updated",
        {
            "name": "metadata_mentions_explicit_null",
            "raw": {
                "id": "msg-0011",
                "content": "edited, explicit null mentions",
                "message_type": "text",
                "sender_type": "User",
                "sender_id": "user-kk11",
                "metadata": {"mentions": None},
                "attachments": [],
                "inserted_at": "2026-08-01T11:10:00Z",
                "updated_at": "2026-08-01T11:12:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "msg-0011",
                    "content": "edited, explicit null mentions",
                    "message_type": "text",
                    "sender_type": "User",
                    "sender_id": "user-kk11",
                    "metadata": {"mentions": []},
                    "attachments": [],
                    "inserted_at": "2026-08-01T11:10:00Z",
                    "updated_at": "2026-08-01T11:12:00Z",
                },
            },
        },
    ),
    (
        "participant_added",
        {
            "name": "user_participant_with_handle_and_role",
            "raw": {
                "id": "user-pa01",
                "type": "User",
                "name": "Riley Chen",
                "handle": "riley",
                "role": "member",
                "status": "active",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "user-pa01",
                    "type": "User",
                    "name": "Riley Chen",
                    "handle": "riley",
                    "role": "member",
                    "status": "active",
                },
            },
        },
    ),
    (
        "participant_added",
        {
            "name": "user_participant_no_handle",
            "raw": {"id": "user-pa02", "type": "User", "name": "Sam Ok"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "user-pa02", "type": "User", "name": "Sam Ok"},
            },
        },
    ),
    (
        "participant_added",
        {
            "name": "agent_participant_with_is_external",
            "raw": {
                "id": "agent-pa03",
                "type": "Agent",
                "name": "Sage",
                "description": "Research assistant",
                "is_external": True,
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "agent-pa03",
                    "type": "Agent",
                    "name": "Sage",
                    "description": "Research assistant",
                    "is_external": True,
                    "is_remote": True,
                },
            },
        },
    ),
    (
        "participant_added",
        {
            "name": "is_remote_only_supplied_mirrors_to_is_external",
            "raw": {
                "id": "agent-pa04",
                "type": "Agent",
                "name": "Nova",
                "is_remote": False,
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "agent-pa04",
                    "type": "Agent",
                    "name": "Nova",
                    "is_remote": False,
                    "is_external": False,
                },
            },
        },
    ),
    (
        "participant_added",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "id": "user-pa05",
                "type": "User",
                "name": "Extra",
                "x_custom": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "user-pa05",
                    "type": "User",
                    "name": "Extra",
                    "x_custom": "keep-me",
                },
            },
        },
    ),
    (
        "participant_added",
        {
            "name": "missing_required_name",
            "raw": {"id": "user-pa06", "type": "User"},
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "name",
                        "code": "missing",
                        "message": "required field `name` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "soft_delete_full_shape_matches_participant_added",
            "raw": {
                "id": "user-pa01",
                "type": "User",
                "name": "Riley Chen",
                "handle": "riley",
                "role": "member",
                "status": "active",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "user-pa01",
                    "type": "User",
                    "name": "Riley Chen",
                    "handle": "riley",
                    "role": "member",
                    "status": "active",
                },
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "user_participant_no_handle",
            "raw": {"id": "user-pa02", "type": "User", "name": "Sam Ok"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "user-pa02", "type": "User", "name": "Sam Ok"},
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "agent_participant_with_is_external",
            "raw": {
                "id": "agent-pa03",
                "type": "Agent",
                "name": "Sage",
                "description": "Research assistant",
                "is_external": True,
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "agent-pa03",
                    "type": "Agent",
                    "name": "Sage",
                    "description": "Research assistant",
                    "is_external": True,
                    "is_remote": True,
                },
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "is_remote_only_supplied_mirrors_to_is_external",
            "raw": {
                "id": "agent-pa04",
                "type": "Agent",
                "name": "Nova",
                "is_remote": False,
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "agent-pa04",
                    "type": "Agent",
                    "name": "Nova",
                    "is_remote": False,
                    "is_external": False,
                },
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "id": "user-pa05",
                "type": "User",
                "name": "Extra",
                "x_custom": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "user-pa05",
                    "type": "User",
                    "name": "Extra",
                    "x_custom": "keep-me",
                },
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "missing_required_name",
            "raw": {"id": "user-pa06", "type": "User"},
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "name",
                        "code": "missing",
                        "message": "required field `name` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "participant_removed",
        {
            "name": "hard_delete_minimal_fallback",
            "raw": {"id": "user-pa07", "type": "User", "name": "Removed Participant"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "user-pa07",
                    "type": "User",
                    "name": "Removed Participant",
                },
            },
        },
    ),
    (
        "room_added",
        {
            "name": "live_five_field_shape",
            "raw": {
                "id": "room-ra01",
                "title": "Q3 planning",
                "task_id": "task-aa01",
                "inserted_at": "2026-07-01T09:00:00Z",
                "updated_at": "2026-07-15T12:30:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "room-ra01",
                    "title": "Q3 planning",
                    "task_id": "task-aa01",
                    "inserted_at": "2026-07-01T09:00:00Z",
                    "updated_at": "2026-07-15T12:30:00Z",
                },
            },
        },
    ),
    (
        "room_added",
        {
            "name": "task_id_and_title_explicit_null",
            "raw": {
                "id": "room-ra02",
                "title": None,
                "task_id": None,
                "inserted_at": "2026-07-02T09:00:00Z",
                "updated_at": "2026-07-02T09:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "room-ra02",
                    "title": None,
                    "task_id": None,
                    "inserted_at": "2026-07-02T09:00:00Z",
                    "updated_at": "2026-07-02T09:00:00Z",
                },
            },
        },
    ),
    (
        "room_added",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "id": "room-ra03",
                "title": "Has an extra field",
                "task_id": None,
                "inserted_at": "2026-07-03T09:00:00Z",
                "updated_at": "2026-07-03T09:00:00Z",
                "x_future_field": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "room-ra03",
                    "title": "Has an extra field",
                    "task_id": None,
                    "inserted_at": "2026-07-03T09:00:00Z",
                    "updated_at": "2026-07-03T09:00:00Z",
                    "x_future_field": "keep-me",
                },
            },
        },
    ),
    (
        "room_added",
        {
            "name": "missing_required_timestamp",
            "raw": {
                "id": "room-ra04",
                "title": "Missing updated_at",
                "task_id": None,
                "inserted_at": "2026-07-04T09:00:00Z",
            },
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "updated_at",
                        "code": "missing",
                        "message": "required field `updated_at` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "room_deleted",
        {
            "name": "minimal_valid",
            "raw": {"id": "room-rd01"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "room-rd01"},
            },
        },
    ),
    (
        "room_deleted",
        {
            "name": "unknown_passthrough_field",
            "raw": {"id": "room-rd02", "x_future_field": "keep-me"},
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {"id": "room-rd02", "x_future_field": "keep-me"},
            },
        },
    ),
    (
        "room_deleted",
        {
            "name": "missing_id",
            "raw": {},
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "id",
                        "code": "missing",
                        "message": "required field `id` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "room_removed",
        {
            "name": "live_five_field_shape",
            "raw": {
                "id": "room-rr01",
                "title": "Q3 planning",
                "task_id": "task-aa01",
                "inserted_at": "2026-07-01T09:00:00Z",
                "updated_at": "2026-07-15T12:30:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "room-rr01",
                    "title": "Q3 planning",
                    "task_id": "task-aa01",
                    "inserted_at": "2026-07-01T09:00:00Z",
                    "updated_at": "2026-07-15T12:30:00Z",
                },
            },
        },
    ),
    (
        "room_removed",
        {
            "name": "task_id_and_title_explicit_null",
            "raw": {
                "id": "room-rr02",
                "title": None,
                "task_id": None,
                "inserted_at": "2026-07-02T09:00:00Z",
                "updated_at": "2026-07-02T09:00:00Z",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "room-rr02",
                    "title": None,
                    "task_id": None,
                    "inserted_at": "2026-07-02T09:00:00Z",
                    "updated_at": "2026-07-02T09:00:00Z",
                },
            },
        },
    ),
    (
        "room_removed",
        {
            "name": "unknown_passthrough_field",
            "raw": {
                "id": "room-rr03",
                "title": "Has an extra field",
                "task_id": None,
                "inserted_at": "2026-07-03T09:00:00Z",
                "updated_at": "2026-07-03T09:00:00Z",
                "x_future_field": "keep-me",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "id": "room-rr03",
                    "title": "Has an extra field",
                    "task_id": None,
                    "inserted_at": "2026-07-03T09:00:00Z",
                    "updated_at": "2026-07-03T09:00:00Z",
                    "x_future_field": "keep-me",
                },
            },
        },
    ),
    (
        "room_removed",
        {
            "name": "missing_required_timestamp",
            "raw": {
                "id": "room-rr04",
                "title": "Missing updated_at",
                "task_id": None,
                "inserted_at": "2026-07-04T09:00:00Z",
            },
            "canonical": {
                "decision": "reject",
                "issues": [
                    {
                        "path": "updated_at",
                        "code": "missing",
                        "message": "required field `updated_at` is missing",
                    }
                ],
                "normalized": None,
            },
        },
    ),
    (
        "supersede",
        {
            "name": "live_shape_agent_evicted",
            "raw": {
                "reason": "session.already_connected",
                "message": "This connection has been superseded by a newer session for this agent.",
                "retryable": False,
                "retry_after": 30,
                "target_socket_id": "agent_socket:agent-sp01",
                "correlation_id": "evict-AbCdEfGh12==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "reason": "session.already_connected",
                    "message": "This connection has been superseded by a newer session for this agent.",
                    "retryable": False,
                    "retry_after": 30,
                    "target_socket_id": "agent_socket:agent-sp01",
                    "correlation_id": "evict-AbCdEfGh12==",
                },
            },
        },
    ),
    (
        "supersede",
        {
            "name": "correlation_id_explicit_null_crdt_eviction",
            "raw": {
                "reason": "session.already_connected",
                "message": "This connection has been superseded by a newer session for this agent.",
                "retryable": False,
                "retry_after": 30,
                "target_socket_id": "agent_socket:agent-sp02",
                "correlation_id": None,
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "reason": "session.already_connected",
                    "message": "This connection has been superseded by a newer session for this agent.",
                    "retryable": False,
                    "retry_after": 30,
                    "target_socket_id": "agent_socket:agent-sp02",
                    "correlation_id": None,
                },
            },
        },
    ),
    (
        "supersede",
        {
            "name": "retry_after_malformed_string",
            "raw": {
                "reason": "session.already_connected",
                "message": "This connection has been superseded by a newer session for this agent.",
                "retryable": False,
                "retry_after": "soon",
                "target_socket_id": "agent_socket:agent-sp03",
                "correlation_id": "evict-ZzYyXxWw99==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "reason": "session.already_connected",
                    "message": "This connection has been superseded by a newer session for this agent.",
                    "retryable": False,
                    "retry_after": None,
                    "target_socket_id": "agent_socket:agent-sp03",
                    "correlation_id": "evict-ZzYyXxWw99==",
                },
            },
        },
    ),
    (
        "supersede",
        {
            "name": "retryable_true_hypothetical",
            "raw": {
                "reason": "session.already_connected",
                "message": "This connection has been superseded by a newer session for this agent.",
                "retryable": True,
                "retry_after": 30,
                "target_socket_id": "agent_socket:agent-sp04",
                "correlation_id": "evict-Hyp0thetica1==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "reason": "session.already_connected",
                    "message": "This connection has been superseded by a newer session for this agent.",
                    "retryable": True,
                    "retry_after": 30,
                    "target_socket_id": "agent_socket:agent-sp04",
                    "correlation_id": "evict-Hyp0thetica1==",
                },
            },
        },
    ),
    (
        "supersede",
        {
            "name": "unknown_reason_code",
            "raw": {
                "reason": "org.maintenance_window",
                "message": "Scheduled maintenance disconnect.",
                "retryable": False,
                "retry_after": 60,
                "target_socket_id": "agent_socket:agent-sp05",
                "correlation_id": "evict-Un8nownReas0n==",
            },
            "canonical": {
                "decision": "accept",
                "issues": [],
                "normalized": {
                    "reason": "org.maintenance_window",
                    "message": "Scheduled maintenance disconnect.",
                    "retryable": False,
                    "retry_after": 60,
                    "target_socket_id": "agent_socket:agent-sp05",
                    "correlation_id": "evict-Un8nownReas0n==",
                },
            },
        },
    ),
]
