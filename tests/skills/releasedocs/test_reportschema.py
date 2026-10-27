"""The report schema passes intact through claude-code-action's argument parsing."""

from __future__ import annotations

from types import ModuleType


class TestReportSchema:
    def test_schema_is_one_line_without_shell_specials(
        self, reportschema: ModuleType
    ) -> None:
        schema = reportschema.cli_schema()
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
