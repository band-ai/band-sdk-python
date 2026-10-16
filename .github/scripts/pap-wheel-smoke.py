"""Exercise PAP discovery from an installed wheel without agent extras."""

from __future__ import annotations

import logging

from bandpap.discovery import DiscoveryDocument, verify_discovery_binding

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
