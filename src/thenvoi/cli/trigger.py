"""
Thenvoi trigger — create a chatroom and send a message to a target agent.

Use this to trigger a Thenvoi process from cron jobs, CI/CD pipelines, GitHub Actions,
or any external automation.

Configuration is accepted via CLI arguments and environment variables (CLI takes precedence).

Exit codes:
    0 — success (prints chatroom ID to stdout)
    1 — failure (prints error message to stderr)

Examples:
    # Agent authentication
    thenvoi-trigger \\
        --api-key "$THENVOI_API_KEY" \\
        --target-handle "@owner/my-agent" \\
        --message "Run the daily report"

    # User authentication
    thenvoi-trigger \\
        --api-key "$THENVOI_USER_API_KEY" \\
        --auth-mode user \\
        --target-handle "@owner/my-agent" \\
        --message "Please review the latest PR"

    # Using environment variables
    THENVOI_API_KEY=key THENVOI_TARGET_HANDLE=@owner/agent \\
        thenvoi-trigger --message "Hello"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Final

from thenvoi.runtime.types import normalize_handle
from thenvoi_rest import (
    AsyncRestClient,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    ChatRoomRequest,
    ParticipantRequest,
)
from thenvoi_rest.core.api_error import ApiError
from thenvoi_rest.core.request_options import RequestOptions
from thenvoi_rest.human_api_chats.types.create_my_chat_room_request_chat import (
    CreateMyChatRoomRequestChat,
)

logger = logging.getLogger(__name__)

DEFAULT_REST_URL = "https://app.thenvoi.com/"
DEFAULT_REQUEST_OPTIONS: Final[RequestOptions] = {"max_retries": 3}
DEFAULT_TIMEOUT: Final[int] = 120


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="thenvoi-trigger",
        description="Create a Thenvoi chatroom and send a message to a target agent.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("THENVOI_API_KEY"),
        help="API key for authentication (env: THENVOI_API_KEY). Prefer env var over CLI to avoid exposing the key in process listings.",
    )
    parser.add_argument(
        "--rest-url",
        default=os.environ.get("THENVOI_REST_URL", DEFAULT_REST_URL),
        help=(
            f"Thenvoi REST API URL (env: THENVOI_REST_URL, default: {DEFAULT_REST_URL})"
        ),
    )
    parser.add_argument(
        "--auth-mode",
        choices=["agent", "user"],
        default=os.environ.get("THENVOI_AUTH_MODE", "agent"),
        help=(
            "Authentication mode: 'agent' or 'user' "
            "(env: THENVOI_AUTH_MODE, default: agent)"
        ),
    )
    parser.add_argument(
        "--target-handle",
        default=os.environ.get("THENVOI_TARGET_HANDLE"),
        help=(
            "Handle of the target agent, e.g. @owner/agent-name "
            "(env: THENVOI_TARGET_HANDLE)"
        ),
    )
    parser.add_argument(
        "--message",
        default=os.environ.get("THENVOI_MESSAGE"),
        help="Message to send to the target agent (env: THENVOI_MESSAGE)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("THENVOI_TRIGGER_TIMEOUT", str(DEFAULT_TIMEOUT))),
        help=(
            "Timeout in seconds for the entire operation "
            f"(env: THENVOI_TRIGGER_TIMEOUT, default: {DEFAULT_TIMEOUT})"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose logging",
    )
    return parser


def _format_api_error(err: ApiError, action: str) -> str:
    """Extract a human-readable message from a Fern ApiError."""
    body = getattr(err, "body", None)
    error_obj = getattr(body, "error", None)
    message = getattr(error_obj, "message", None)
    if message:
        return f"Failed to {action}: {message}"
    return f"Failed to {action}: HTTP {err.status_code}"


async def find_peer_by_handle(
    client: AsyncRestClient,
    handle: str,
    auth_mode: str,
) -> dict[str, str] | None:
    """
    Paginate through peers to find one matching the given handle.

    Returns dict with 'id', 'name', 'handle' or None if not found.
    """
    normalized = (normalize_handle(handle) or "").lower()
    page = 1
    while True:
        if auth_mode == "agent":
            response = await client.agent_api_peers.list_agent_peers(
                page=page,
                page_size=100,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        else:
            response = await client.human_api_peers.list_my_peers(
                page=page,
                page_size=100,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )

        if not response.data:
            break

        for peer in response.data:
            peer_handle = getattr(peer, "handle", None) or ""
            if (normalize_handle(peer_handle) or "").lower() == normalized:
                return {
                    "id": peer.id,
                    "name": peer.name,
                    "handle": peer_handle,
                }

        logger.debug(
            "Scanned %d peers on page %d, no match for '%s'",
            len(response.data),
            page,
            normalized,
        )

        total_pages = (
            getattr(response.metadata, "total_pages", None) or 1
            if response.metadata
            else 1
        )
        if page >= total_pages:
            break
        page += 1

    return None


async def run(args: argparse.Namespace) -> str:
    """
    Execute the trigger flow.

    Returns the chatroom ID on success.
    Raises ValueError or RuntimeError on failure.
    """
    if not args.api_key:
        raise ValueError(
            "API key is required. Provide --api-key or set THENVOI_API_KEY."
        )
    if not args.target_handle:
        raise ValueError(
            "Target handle is required. "
            "Provide --target-handle or set THENVOI_TARGET_HANDLE."
        )
    if not args.message:
        raise ValueError(
            "Message is required. Provide --message or set THENVOI_MESSAGE."
        )

    client = AsyncRestClient(api_key=args.api_key, base_url=args.rest_url.rstrip("/"))

    try:
        # Step 1: Look up the target peer by handle
        logger.info("Looking up peer with handle: %s", args.target_handle)
        peer = await find_peer_by_handle(client, args.target_handle, args.auth_mode)
        if not peer:
            raise ValueError(
                f"Target agent with handle '{args.target_handle}' not found. "
                "Verify the handle is correct and that you have access to this peer."
            )
        logger.info("Found peer: %s (id=%s)", peer["name"], peer["id"])

        # Step 2: Create a new chatroom
        logger.info("Creating chatroom...")
        try:
            if args.auth_mode == "agent":
                chat_response = await client.agent_api_chats.create_agent_chat(
                    chat=ChatRoomRequest(),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            else:
                chat_response = await client.human_api_chats.create_my_chat_room(
                    chat=CreateMyChatRoomRequestChat(),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
        except ApiError as e:
            raise RuntimeError(_format_api_error(e, "create chatroom")) from e
        room_id = chat_response.data.id
        logger.info("Created chatroom: %s", room_id)

        # Step 3: Add the target agent as a participant
        # Step 4: Send the message mentioning the target agent
        # Wrapped in try/except so we log the orphan room ID on partial failure.
        try:
            logger.info("Adding %s to chatroom...", peer["name"])
            if args.auth_mode == "agent":
                await client.agent_api_participants.add_agent_chat_participant(
                    chat_id=room_id,
                    participant=ParticipantRequest(participant_id=peer["id"]),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            else:
                await client.human_api_participants.add_my_chat_participant(
                    chat_id=room_id,
                    participant=ParticipantRequest(participant_id=peer["id"]),
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            logger.info("Added participant: %s", peer["name"])

            logger.info("Sending message...")
            mention = ChatMessageRequestMentionsItem(
                id=peer["id"],
                handle=peer["handle"],
            )
            message_request = ChatMessageRequest(
                content=args.message,
                mentions=[mention],
            )
            if args.auth_mode == "agent":
                await client.agent_api_messages.create_agent_chat_message(
                    chat_id=room_id,
                    message=message_request,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            else:
                await client.human_api_messages.send_my_chat_message(
                    chat_id=room_id,
                    message=message_request,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            logger.info("Message sent successfully")
        except ApiError as e:
            logger.warning(
                "Failed after creating room %s — room may need manual cleanup",
                room_id,
            )
            raise RuntimeError(
                f"{_format_api_error(e, 'complete trigger')} (orphan room: {room_id})"
            ) from e
        except Exception:
            logger.warning(
                "Failed after creating room %s — room may need manual cleanup",
                room_id,
            )
            raise
    finally:
        await client._client_wrapper.httpx_client.httpx_client.aclose()

    return room_id


async def run_with_timeout(args: argparse.Namespace) -> str:
    """Wrapper that applies a timeout to the trigger flow."""
    return await asyncio.wait_for(run(args), timeout=args.timeout)


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        room_id = asyncio.run(run_with_timeout(args))
    except asyncio.TimeoutError:
        sys.stderr.write(f"Error: operation timed out after {args.timeout} seconds\n")
        sys.exit(1)
    except (ValueError, RuntimeError) as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    # stdout carries the machine-readable room ID (intentional print for CLI output)
    sys.stdout.write(room_id + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
