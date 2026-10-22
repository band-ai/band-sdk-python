"""Host-controlled HTTP worker. Its parent enforces one deadline across DNS/TLS/I/O."""

from __future__ import annotations

import http.client
import json
import ssl
import sys

from remote_agent.network import strict_json, validate_url
from remote_agent.storage import canonical
from remote_agent.types import JsonObject


def request(options: JsonObject) -> JsonObject:
    parsed = validate_url(options["url"])
    host = parsed.hostname
    assert host is not None
    arguments = {
        "host": host,
        "port": parsed.port,
        "timeout": options["timeout_ms"] / 1000,
    }
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(
            **arguments, context=ssl.create_default_context(cafile=options["ca_file"])
        )
    else:
        connection = http.client.HTTPConnection(**arguments)
    try:
        body = canonical(options["data"]) if options["data"] is not None else b""
        authentication = options.get("authentication", "bearer")
        if authentication == "bearer":
            credential = {"Authorization": "Bearer " + options["token"]}
        elif authentication == "band-api-key":
            credential = {"X-API-Key": options["token"]}
        else:
            raise ValueError("Unsupported authentication scheme")
        connection.request(
            options["method"],
            parsed.path or "/",
            body=body,
            headers={
                **credential,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        lengths = response.headers.get_all("Content-Length", [])
        if (
            len(lengths) != 1
            or not lengths[0].isdigit()
            or response.headers.get_all("Transfer-Encoding")
            or int(lengths[0]) > 4_194_304
            or response.headers.get_content_type() != "application/json"
        ):
            raise ValueError("Invalid HTTP response framing")
        encoded = response.read(int(lengths[0]) + 1)
        if len(encoded) != int(lengths[0]):
            raise ValueError("Incomplete HTTP response")
        return {"status": response.status, "body": strict_json(encoded)}
    finally:
        connection.close()


def main() -> None:
    try:
        options = json.loads(sys.stdin.buffer.read(4_194_305))
        result = request(options)
    except Exception:  # noqa: BLE001 -- The process boundary redacts every transport exception, including credential-bearing URLs.
        # Network exceptions can contain request URLs. Credentials and room data
        # never appear in the diagnostic crossing the process boundary.
        result = {"error": "HTTP transport failed"}
    sys.stdout.buffer.write(canonical(result))


if __name__ == "__main__":
    main()
