"""LockInputs specification regression checks."""

from __future__ import annotations

import tempfile
import unittest

from tests.contracts.spec.checkerfailures.kit import fake_wheel
from tests.contracts.spec.workspace import isolated_root, run_tool


class TestLockInputs(unittest.TestCase):
    """lockpython.py locked whatever wheels sat in .cache and never read requirements-dev.in (impl L11)."""

    def lock(self, roots: list[str], *wheels: tuple[str, ...]) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            (root / "requirements-dev.in").write_text(
                "# Roots.\n" + "".join(item + "\n" for item in roots)
            )
            cache = root / ".cache/requirements"
            cache.mkdir(parents=True)
            for wheel in wheels:
                fake_wheel(cache, *wheel)
            # PyPI is unreachable here: drift must be refused before any lookup.
            blocked = {
                "https_proxy": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
            }
            result = run_tool(root, "lockpython.py", env=blocked)
            self.assertNotEqual(result.returncode, 0, result.stdout[-300:])
            self.assertFalse((root / "requirements-dev.lock").exists())
            return result.stderr

    def test_drift_from_requirements_dev_in_is_refused(self) -> None:
        cases = {
            "stray": (["a==1.0"], ("a", "1.0", "b>=2"), ("b", "2.0"), ("stray", "1.0")),
            "b": (["a==1.0"], ("a", "1.0", "b>=2")),
            "a==1.1": (["a==1.1"], ("a", "1.0")),
            "b>=3": (["a==1.0"], ("a", "1.0", "b>=3"), ("b", "2.0")),
            "unpinned": (["a>=1.0"], ("a", "1.0")),
        }
        for named, (roots, *wheels) in cases.items():
            with self.subTest(named=named):
                stderr = self.lock(roots, *wheels)
                self.assertIn("requirements-dev.in", stderr)
                self.assertIn(named, stderr)

    def test_the_target_closure_ignores_extras_and_other_platforms(self) -> None:
        stderr = self.lock(
            ["A==1.0"],
            (
                "a",
                "1.0",
                "typing-extensions>=4",
                'c; extra == "x"',
                'd; python_version < "3.11"',
                'e; os_name == "nt"',
            ),
            ("typing_extensions", "4.16.0"),
        )
        self.assertNotIn("requirements-dev.in", stderr)
