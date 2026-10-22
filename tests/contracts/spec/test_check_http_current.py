"""The HTTP checker validates current reply wires and still rejects unknown extensions."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonschema
import pytest

from contracts.spec.tools import checkhttp
from remote_agent.network import HTTPRoom


class TestCurrentHTTPWire(unittest.TestCase):
    def run_checker(self, output: Path) -> None:
        with (
            patch.object(sys, "argv", ["checkhttp.py", "--output", str(output)]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            checkhttp.main()

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    def test_real_reply_exchange_passes_the_published_http_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "http.json"
            self.run_checker(output)
            report = json.loads(output.read_text())
        self.assertTrue(report["passed"])
        self.assertIn("http_definition", report["checks"])
        self.assertIn("http_compact_projection", report["checks"])
        self.assertIn("standalone_inbox_response", report["checks"])

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    def test_unknown_extension_remains_rejected_on_the_actual_definition_wire(
        self,
    ) -> None:
        original = HTTPRoom._request
        unknown = "test.unknown/999"

        def mutate(
            client: HTTPRoom, method: str, path: str, *args: Any, **kwargs: Any
        ) -> dict[str, Any]:
            result = original(client, method, path, *args, **kwargs)
            if path == "/v1/definition":
                result = copy.deepcopy(result)
                result["definition"]["contract"]["card"]["extensions"].append(unknown)
            return result

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(HTTPRoom, "_request", mutate),
            self.assertRaises(jsonschema.ValidationError) as failure,
        ):
            self.run_checker(Path(directory) / "http.json")
        self.assertEqual(failure.exception.validator, "enum")
        self.assertEqual(failure.exception.instance, unknown)


if __name__ == "__main__":
    unittest.main()
