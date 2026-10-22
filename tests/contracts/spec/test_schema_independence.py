"""M17: current schema generation does not load the compatibility builder."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from contracts.spec.tools.buildv2schemas import root
from tests.paths import CONTRACTS_ROOT as ROOT
from tests.paths import REPO_ROOT


class TestIndependentSchema(unittest.TestCase):
    def test_all_current_builders_work_when_v1_import_is_forbidden(self) -> None:
        program = """import builtins
original = builtins.__import__
def checked(name,*args,**kwargs):
    fromlist = kwargs.get('fromlist', args[2] if len(args) > 2 else ())
    if name == 'contracts.spec.tools.buildschemas' or (
        name == 'contracts.spec.tools' and 'buildschemas' in (fromlist or ())
    ):
        raise AssertionError('current builders imported the compatibility schema')
    return original(name,*args,**kwargs)
builtins.__import__ = checked
import contracts.spec.tools.buildv2schemas
import contracts.spec.tools.buildcoordinationschemas
import contracts.spec.tools.buildparticipantschemas
import contracts.spec.tools.buildcontributionschemas
from contracts.spec.tools import buildrepliesschemas
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_schema_still_reproduces_the_reviewed_registry(self) -> None:
        self.assertEqual(
            root,
            json.loads((ROOT / "spec/schemas/protocol-v2.schema.json").read_text()),
        )


if __name__ == "__main__":
    unittest.main()
