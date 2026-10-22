from __future__ import annotations

import unittest

from remote_agent.protocol import canonical, decode


class TestProtocol(unittest.TestCase):
    def test_cross_language_canonical_control_escapes_and_unicode(self) -> None:
        value = {"z": "\x1f\v\b\n\t\f\r", "é": "👩🏽‍💻", "a": "\\u001f\\u000b\\\\\x1a"}
        self.assertEqual(
            canonical(value),
            '{"a":"\\\\u001f\\\\u000b\\\\\\\\\\u001A","z":"\\u001F\\u000B\\b\\n\\t\\f\\r","é":"👩🏽‍💻"}'.encode(),
        )
        self.assertEqual(decode(canonical(value)), value)

    def test_nonportable_numbers_surrogates_and_duplicate_keys_are_rejected(
        self,
    ) -> None:
        for value in [0.5, float("nan"), 2**53, "\ud800", {1: "x"}]:
            with self.assertRaises((ValueError, UnicodeError)):
                canonical(value)
        for encoded in [b'{"a":1,"a":2}', b"1.0", b"9007199254740992", b'"\\ud800"']:
            with self.assertRaises((ValueError, UnicodeError)):
                decode(encoded)
