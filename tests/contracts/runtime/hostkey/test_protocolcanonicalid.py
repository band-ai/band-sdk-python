from __future__ import annotations

import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from remote_agent.hostkey import HostKey
from remote_agent.protocol import canonical as protocol_canonical
from remote_agent.storage import canonical
from remote_agent.store import Store


class TestProtocolCanonicalId(unittest.TestCase):
    def test_control_unicode_and_literal_escape_tuple_uses_protocol_not_journal_encoding(
        self,
    ) -> None:
        parameters: tuple[str, str, str, int] = (
            "s\u000bé",
            "a\u001fé",
            "literal\\u000b\u001bé",
            1,
        )
        values = ["band-remote-id/1", *parameters]
        expected_protocol = b'["band-remote-id/1","s\\u000B\xc3\xa9","a\\u001F\xc3\xa9","literal\\\\u000b\\u001B\xc3\xa9",1]'
        expected_journal = b'["band-remote-id/1","s\\u000b\xc3\xa9","a\\u001f\xc3\xa9","literal\\\\u000b\\u001b\xc3\xa9",1]'
        self.assertEqual(protocol_canonical(values), expected_protocol)
        self.assertEqual(
            canonical(values),
            expected_journal,
            "Historical journal canonical encoding changed",
        )
        with (
            tempfile.TemporaryDirectory(prefix="h7-canonical-") as directory,
            Store(Path(directory) / "state.sqlite3") as store,
        ):
            host = HostKey(store)
            actual = host.identifier(*parameters)
            expected = (
                "remote:"
                + hmac.new(
                    host.path.read_bytes(), expected_protocol, hashlib.sha256
                ).hexdigest()[:40]
            )
            self.assertTrue(
                actual == expected,
                "HMAC used historical journal escaping instead of protocol canonical bytes",
            )
