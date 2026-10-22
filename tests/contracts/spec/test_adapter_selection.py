"""M20: an explicitly selected invalid adapter must never become the reference."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from contracts.spec.tools import checkspec, generatevectors


class TestAdapterSelection(unittest.TestCase):
    def test_falsy_commands_fail_before_transport(self) -> None:
        invalid_argv: tuple[Any, ...] = ([], {}, False, 0, "")
        for argv in invalid_argv:
            with (
                self.subTest(argv=argv),
                patch.object(
                    generatevectors, "run_adapter", return_value=b'{"ok":1}\n'
                ) as transport,
            ):
                with self.assertRaises(ValueError):
                    generatevectors.adapter([{"op": "canonical", "value": 1}], argv)
                transport.assert_not_called()

    def test_explicit_json_null_is_not_the_default_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "adapter.json"
            config.write_text("null")
            manifest = json.loads(
                (checkspec.ROOT / "spec/conformance/manifest.json").read_text()
            )
            responses = []
            for row in manifest["cases"]:
                if row["id"].startswith("carrier-"):
                    responses.extend(
                        json.loads(
                            (
                                checkspec.ROOT / "spec/conformance" / row["path"]
                            ).read_text()
                        )["expected"]
                    )
            with (
                patch.object(checkspec, "adapter", return_value=responses) as transport,
                patch(
                    "sys.argv",
                    [
                        "checkspec",
                        "--case-prefix",
                        "carrier-",
                        "--adapter-config",
                        str(config),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(ValueError):
                    checkspec.main()
                transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
