"""The reference adapter restores reply sessions as the full reference host it is.

Its restore op sends the host's capabilities: every extension the engine implements unless the
request states its own list, which is then checked exactly as at session start (Sofia O1).
"""

from __future__ import annotations

import hashlib
import unittest

import pytest

from contracts.spec.tools.generatevectors import adapter

SOURCE = (
    "---\nname: deploy\ndescription: Ok and error replies.\nengine: 0.2.0-lab.1\n"
    "roles: [lead, deployer]\n---\ncontract @1h => expired\ntype deploy\ntype ok\n"
    "  request: ref(deploy)\nstate new\n  lead -> deployer: deploy() as job awaits deploying\n"
    "    => ok(request=$job) & deployed\n     | @10m & stalled\n"
    "end deployed\nend stalled\n"
)
FILE_SOURCE = {
    "profile": "band-contract/file-source/1",
    "format": "frontmatter",
    "content": SOURCE,
    "sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
}


class TestRepliesRestore(unittest.TestCase):
    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_restore_uses_the_reference_host_capabilities_unless_the_request_states_its_own(
        self,
    ) -> None:
        config = {
            "session": "s",
            "bindings": {"lead": "l", "deployer": "d"},
            "now": 0,
            "inactivity": "accepted",
            "max_messages": 20,
            "room_policy": {"mode": "unchecked"},
            "capabilities": ["contract.replies/1"],
        }
        first = adapter(
            [
                {"op": "compile", "handle": "c", "source": FILE_SOURCE},
                {"op": "new", "contract": "c", "handle": "s", "config": config},
                {"op": "checkpoint", "handle": "s"},
            ]
        )
        self.assertNotIn("error", first[1], first[1])
        packet = first[2]["ok"]
        restore = {
            "op": "restore",
            "contract": "c",
            "handle": "r",
            "checkpoint": packet,
            "now": 5,
        }
        compile_op = {"op": "compile", "handle": "c", "source": FILE_SOURCE}
        out = adapter(
            [
                compile_op,
                restore,
                {**restore, "capabilities": []},
                {**restore, "capabilities": ["contract.replies/1"]},
            ]
        )
        self.assertIn("ok", out[1], out[1])
        self.assertEqual(out[2], {"error": "unsupported_extension"})
        self.assertIn("ok", out[3], out[3])


if __name__ == "__main__":
    unittest.main()
