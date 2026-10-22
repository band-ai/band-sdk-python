"""Preserve accepted author output when its first completion acknowledgment is lost."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from tests.contracts.runtime.authorprocess import (
    AuthorPeer,
    AuthorProcess,
    author_configuration,
)


class TestAuthorProcess(unittest.TestCase):
    @pytest.mark.contract_runtime
    def test_restart_delivers_the_same_completion_without_resampling(self) -> None:
        with tempfile.TemporaryDirectory(prefix="band-author-process-") as directory:
            root = Path(directory)
            peer = AuthorPeer()
            self.addCleanup(peer.close)
            configuration, calls = author_configuration(root, peer)
            first = AuthorProcess(root, configuration)
            self.addCleanup(first.close)
            self.assertEqual(first.readiness(), {"event": "protocol_author_ready"})
            self.assertTrue(
                peer.first_completion.wait(8),
                "The selected author did not complete the claimed job",
            )
            first.close()
            second = AuthorProcess(root, configuration)
            self.addCleanup(second.close)
            self.assertEqual(second.readiness(), {"event": "protocol_author_ready"})
            self.assertTrue(
                peer.acknowledged.wait(8),
                "Restart did not reconcile the saved completion",
            )
            second.close()
            self.assertEqual(len(peer.responses), 2)
            self.assertEqual(peer.responses[0], peer.responses[1])
            self.assertEqual(json.loads(peer.responses[0])["attempt"], 1)
            self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["call"])
