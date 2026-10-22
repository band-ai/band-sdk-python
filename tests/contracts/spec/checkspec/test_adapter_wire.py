"""AdapterWire specification regression checks."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from contracts.spec.tools import generatevectors


class TestAdapterWire(unittest.TestCase):
    def test_adapter_responses_must_be_strict_utf8_protocol_json(self) -> None:
        invalid = [
            b'{"ok":1,"ok":2}\n',
            b'{"ok":{"x":1,"x":2}}\n',
            b'{"ok":1.0}\n',
            b'{"ok":1e0}\n',
            b'{"ok":NaN}\n',
            b'{"ok":9007199254740992}\n',
            b'{"ok":"\\ud800"}\n',
            '{"ok":1}'.encode("utf-16-le") + b"\n",
        ]
        for output in invalid:
            with (
                self.subTest(output=output),
                patch.object(generatevectors, "run_adapter", return_value=output),
                self.assertRaises((ValueError, UnicodeError)),
            ):
                generatevectors.adapter(
                    [{"op": "canonical", "value": None}], ["test-adapter"]
                )

    def test_adapter_permits_valid_noncanonical_json_and_preserves_response_count(
        self,
    ) -> None:
        with patch.object(
            generatevectors,
            "run_adapter",
            return_value=' {"ok": "é", "items": [true, 1, null]} \n'.encode(),
        ):
            self.assertEqual(
                generatevectors.adapter(
                    [{"op": "canonical", "value": None}], ["test-adapter"]
                ),
                [{"ok": "é", "items": [True, 1, None]}],
            )
        with (
            patch.object(generatevectors, "run_adapter", return_value=b"{}\n{}\n"),
            self.assertRaises(AssertionError),
        ):
            generatevectors.adapter(
                [{"op": "canonical", "value": None}], ["test-adapter"]
            )
