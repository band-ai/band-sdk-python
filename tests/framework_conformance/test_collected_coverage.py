"""Collection coverage follows pytest's actual keyword selection and framework bindings."""

from __future__ import annotations

from pathlib import Path

from tests.framework_conformance.venv_job_coverage import (
    collected_nodes,
    selected_nodes,
)


class TestCollectedCoverage:
    def test_keyword_filter_does_not_cover_an_explicitly_named_file(
        self, tmp_path: Path
    ) -> None:
        test = tmp_path / "test_selection.py"
        test.write_text(
            "class TestSelection:\n"
            "    def test_retained(self): pass\n"
            "    def test_omitted(self): pass\n"
        )
        full = selected_nodes([str(test)])
        selected = selected_nodes([str(test), "-k", "retained"])
        assert {node.rsplit("::", 1)[1] for node in full - selected} == {"test_omitted"}

    def test_framework_binding_does_not_depend_on_the_test_name(
        self, tmp_path: Path
    ) -> None:
        test = tmp_path / "test_bindings.py"
        test.write_text(
            "import pytest\n"
            "class TestBindings:\n"
            '    @pytest.mark.parametrize("framework_id", ["strands", "pydantic_ai"])\n'
            "    def test_behavior(self, framework_id): pass\n"
        )
        nodes = collected_nodes([str(test)])
        assert {node.frameworks for node in nodes} == {
            frozenset({"strands"}),
            frozenset({"pydantic_ai"}),
        }
