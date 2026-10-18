"""Conformance imports remain usable without the optional Claude SDK extra."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_adapter_module_imports_without_the_optional_sdk() -> None:
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class MissingSDK(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'claude_agent_sdk' or fullname.startswith('claude_agent_sdk.'):
                    raise ModuleNotFoundError('Optional SDK unavailable', name=fullname)

        sys.meta_path.insert(0, MissingSDK())
        import band.adapters.claude_sdk as adapter_module
        assert not adapter_module._CLAUDE_SDK_AVAILABLE
        try:
            adapter_module.ClaudeSDKAdapter()
        except ImportError:
            pass
        else:
            raise AssertionError('Unavailable SDK accepted by adapter')
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
