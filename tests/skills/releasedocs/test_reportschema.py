"""The report schema passes intact through claude-code-action's argument parsing."""

from __future__ import annotations

from types import ModuleType

import pytest


class TestReportSchema:
    @pytest.mark.parametrize("kind", ["changelog", "holdbacks", "docs"])
    def test_schema_is_one_line_without_shell_specials(
        self, reportschema: ModuleType, kind: str
    ) -> None:
        schema = reportschema.cli_schema(reportschema.ReportKind(kind))
        assert ("\n" in schema, "$" in schema, "'" in schema, schema[0]) == (
            False,
            False,
            False,
            "{",
        )

    def test_schema_inlines_every_model(self, reportschema: ModuleType) -> None:
        schema = reportschema.cli_schema()
        assert '"title":"Item"' in schema
        assert '"title":"Breaking"' in schema
