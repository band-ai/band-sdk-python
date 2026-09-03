"""Room-file input models -- gated behind ``Capability.FILES``.

See ``chat`` for the single-source-of-truth-for-schemas note that applies to
every input model in this package.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListRoomFilesInput(BaseModel):
    """List files that have been shared in the current room.

    Returns attachment metadata for every file attached to a message you
    sent or were mentioned in, including ones sent before you joined. Use
    band_read_room_file with a returned id to fetch its contents.
    """

    cursor: str | None = Field(
        None, description="Pagination cursor from a previous call's response"
    )


class ReadRoomFileInput(BaseModel):
    """Read a file shared in the current room.

    Returns the decoded text for a small text file, an image for a small
    previewable image, or a name/type/size description when the file is too
    large or not previewable to show inline.
    """

    file_id: str = Field(
        ...,
        description=(
            "File ID, from a message's attachments or band_list_room_files. "
            "Use the id from the most recent band_list_room_files call, not one "
            "remembered from earlier in the conversation -- files can expire or "
            "be replaced."
        ),
    )


class SendRoomFileInput(BaseModel):
    """Upload text content as a file and share it in the current room.

    Use this to hand participants a file you composed (e.g. a report, a code
    snippet, generated data) rather than pasting it into the message body.
    """

    content: str = Field(..., description="Text content to upload as a file")
    filename: str = Field(
        ...,
        description=(
            "Name for the uploaded file, including extension. Plain ASCII "
            "only (e.g. 'report.txt') -- accents, CJK, emoji, and other "
            "non-ASCII characters are rejected."
        ),
    )
    caption: str = Field(
        "", description="Optional message text to send alongside the file"
    )
    mentions: list[str] = Field(
        ...,
        description=(
            "List of participant handles to @mention. At least one required -- "
            "sharing a file still posts a message, and the platform requires "
            "every message to mention at least one recipient. Same format as "
            "band_send_message."
        ),
    )
