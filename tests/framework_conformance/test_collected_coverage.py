"""Collection coverage follows pytest's actual keyword selection and framework bindings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.framework_conformance.venv_job_coverage import (
    collected_nodes,
    selected_nodes,
)


class TestCollectedCoverage:
    def test_keyword_filter_does_not_cover_an_explicitly_named_file(
        self, tmp_path: Path, pytester: pytest.Pytester
    ) -> None:
        test = tmp_path / "test_selection.py"
        test.write_text(
            "class TestSelection:\n"
            "    def test_retained(self): pass\n"
            "    def test_omitted(self): pass\n"
        )
        full = selected_nodes(pytester, [str(test)])
        selected = selected_nodes(pytester, [str(test), "-k", "retained"])
        assert {node.rsplit("::", 1)[1] for node in full - selected} == {"test_omitted"}

    def test_framework_binding_does_not_depend_on_the_test_name(
        self, tmp_path: Path, pytester: pytest.Pytester
    ) -> None:
        test = tmp_path / "test_bindings.py"
        test.write_text(
            "import pytest\n"
            "class TestBindings:\n"
            '    @pytest.mark.parametrize("framework_id", ["strands", "pydantic_ai"])\n'
            "    def test_behavior(self, framework_id): pass\n"
        )
        nodes = collected_nodes(pytester, [str(test)])
        assert {node.frameworks for node in nodes} == {
            frozenset({"strands"}),
            frozenset({"pydantic_ai"}),
        }

    def test_ignored_file_does_not_count_as_covered(
        self, pytester: pytest.Pytester
    ) -> None:
        pytester.makeini("[pytest]")
        retained = pytester.makepyfile(test_retained="def test_behavior(): pass")
        omitted = pytester.makepyfile(test_omitted="def test_behavior(): pass")
        full = selected_nodes(pytester, [str(pytester.path)])
        selected = selected_nodes(
            pytester, [str(pytester.path), "--ignore", str(omitted)]
        )
        assert {node.split("::", 1)[0] for node in full - selected} == {omitted.name}
        assert {node.split("::", 1)[0] for node in selected} == {retained.name}

    def test_collection_restores_imported_modules(
        self, pytester: pytest.Pytester
    ) -> None:
        test = pytester.makepyfile(test_isolated_import="def test_behavior(): pass")
        assert selected_nodes(pytester, [str(test)])
        assert test.stem not in sys.modules

    def test_collection_errors_fail_the_coverage_check(
        self, pytester: pytest.Pytester
    ) -> None:
        test = pytester.makepyfile('raise RuntimeError("collection failed")')
        with pytest.raises(AssertionError):
            collected_nodes(pytester, [str(test)])
