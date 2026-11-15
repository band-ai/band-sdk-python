"""Send contact notifications through the local Phoenix peer."""

from __future__ import annotations

from band_sdk_core import AgentTopicKind
from phoenix_channels_python_client import ProtocolVersion
from phoenix_channels_python_client.codec import encode_message
from phoenix_channels_python_client.utils import make_message
from websockets.asyncio.server import ServerConnection

from band.client.streaming import ContactAddedPayload
from band.client.streaming.client import WireEvent
from tests.identifiers import AGENT_ID

CONTACT_TOPIC = AgentTopicKind.Contacts.topic(AGENT_ID)


async def send_contact_event(
    connection: ServerConnection, contact: ContactAddedPayload
) -> None:
    await connection.send(
        encode_message(
            make_message(
                topic=CONTACT_TOPIC,
                event=WireEvent.CONTACT_ADDED,
                payload=contact.model_dump(exclude_none=True),
            ),
            ProtocolVersion.V2,
        )
    )
