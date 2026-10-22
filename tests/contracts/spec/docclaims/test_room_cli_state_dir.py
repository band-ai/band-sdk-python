"""RoomCliStateDir specification regression checks."""

from __future__ import annotations

import re
import unittest

from tests.contracts.spec.docclaims.kit import ROOT
from tests.paths import CONTRACT_RUNTIME_ROOT


class TestRoomCliStateDir(unittest.TestCase):
    """DF-49: the room_cli example creates the state dir 0700 and names the refusal it avoids."""

    def section(self) -> str:
        text = (ROOT / "agent-runtime/README.md").read_text(encoding="utf-8")
        match = re.search(
            r"^## Ordinary agent room CLI\n(.*?)(?=^## |\Z)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        assert match is not None, "The room CLI documentation section is missing"
        return match.group(1)

    def test_example_makes_the_state_dir_before_the_first_call(self) -> None:
        match = re.search(r"```sh\n(.*?)```", self.section(), re.DOTALL)
        assert match is not None, "The room CLI shell example is missing"
        block = match.group(1).splitlines()
        first_call = next(n for n, line in enumerate(block) if "--state-dir" in line)
        made = [
            n for n, line in enumerate(block) if line.strip() == 'mkdir -m 700 "$STATE"'
        ]
        self.assertTrue(
            made and made[0] < first_call,
            'mkdir -m 700 "$STATE" before the first room_cli call',
        )

    def test_prose_quotes_the_cli_refusal(self) -> None:
        cli = (CONTRACT_RUNTIME_ROOT / "room_cli.py").read_text(encoding="utf-8")
        match = re.search(r'ClientError\("(State directory[^"]*)"\)', cli)
        assert match is not None, "The room CLI state-directory refusal is missing"
        refusal = match.group(1)
        section = re.sub(r"\s+", " ", self.section())
        self.assertIn(refusal, section)
        self.assertIn(
            "mkdir -p", section, "say why: mkdir -p leaves the directory group-writable"
        )
