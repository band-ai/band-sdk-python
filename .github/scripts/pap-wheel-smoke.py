"""Exercise PAP discovery from an installed wheel without agent extras."""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from bandpap import PAPAdapterConfig, PAPGatewayAdapterConfig, application
from bandpap.auth import SecurityStore, SigningKey
from bandpap.channels import BrowserClient, MCPClient, OpenAPIClient, OperationsClient
from bandpap.discovery import DiscoveryDocument, verify_discovery_binding
from bandpap.fetch import DiscoveryFetcher
from bandpap.storage import Route, Scope, Session, StateStore
from bandpap.wire import Context, Message, SendMessage

logger = logging.getLogger(__name__)


def main() -> None:
    document = DiscoveryDocument.model_validate(
        {
            "protocol_version": "0.1",
            "organization": {"name": "Example", "domain": "shop.example"},
            "web": {},
        }
    )
    verify_discovery_binding(
        document, fetched_url="https://shop.example/.well-known/poppy.json"
    )
    try:
        verify_discovery_binding(document, fetched_url="https://attacker.example")
    except ValueError:
        logger.info("Installed PAP wheel rejects another domain's discovery")
    else:
        raise AssertionError("Installed PAP wheel accepted a mismatched domain")

    assert PAPAdapterConfig(proxy_id="proxy").proxy_id == "proxy"
    assert PAPGatewayAdapterConfig(proxy_id="proxy").proxy_id == "proxy"
    assert callable(application)
    assert all(
        callable(channel)
        for channel in (
            BrowserClient,
            MCPClient,
            OpenAPIClient,
            OperationsClient,
            SecurityStore,
        )
    )
    key = SigningKey.generate()
    assert key.public_jwk()["kty"] == "EC"
    DiscoveryFetcher()
    Context(locale="en-US", time_zone="America/Los_Angeles")
    session = Session(
        scope=Scope(
            tenant="smoke",
            user="user",
            client="client",
            issuer="https://shop.example",
            direction="outbound",
        ),
        session_id="session",
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "pap.db"
        store = StateStore(path)
        store.connect(
            session,
            "company",
            Route(
                room="room",
                proxy="proxy",
                source="source",
                recipient="source",
                grant="grant",
            ),
        )
        store.create_conversation(session, binding="conversation", connection="company")
        store.enqueue(
            session,
            "conversation",
            SendMessage(message=Message(id="msg_smoke", sender="agent", text="hello")),
        )
        store.close()
        store = StateStore(path)
        assert store.get_delivery(session, "msg_smoke").state == "ready"
        store.close()
    logger.info("Installed PAP wheel preserves queued messages across reopen")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
