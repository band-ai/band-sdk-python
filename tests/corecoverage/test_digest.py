"""Core coverage digest rendering."""

from pathlib import Path

from tests.corecoverage.kit import _load_digest_module


class TestCoreCoverageDigest:
    def test_display_path_falls_back_to_basename_without_a_crates_segment(self) -> None:
        module = _load_digest_module()
        assert module.display_path("/work/other/src/file.rs") == "file.rs"

    def test_coverage_marker_thresholds(self) -> None:
        module = _load_digest_module()
        assert module.coverage_marker(100.0) == "🟢"
        assert module.coverage_marker(module.LOW_COVERAGE_PERCENT) == "🟢"
        assert module.coverage_marker(module.LOW_COVERAGE_PERCENT - 0.01) == "🟠"
        assert module.coverage_marker(module.MID_COVERAGE_PERCENT) == "🟠"
        assert module.coverage_marker(module.MID_COVERAGE_PERCENT - 0.01) == "🔴"
        assert module.coverage_marker(0.0) == "🔴"

    def test_render_digest_reports_unavailable_coverage_when_lcov_is_missing(
        self,
        tmp_path: Path,
    ) -> None:
        module = _load_digest_module()
        digest = module.render_digest(
            lcov_path=tmp_path / "missing.lcov",
            label="Core",
            recipients="@bandzalkin",
            run_url="https://example.test/run",
            result="failure",
        )

        assert "⚠️ **Coverage unavailable** · workflow `failure`" in digest
        assert "No LCOV report was produced" in digest
        assert "[Open run](https://example.test/run)" in digest

    def test_weekly_digest_identifies_low_and_completely_uncovered_files(
        self,
        tmp_path: Path,
    ) -> None:
        module = _load_digest_module()

        lcov = tmp_path / "coverage.lcov"
        lcov.write_text(
            "SF:/work/crates/core/src/covered.rs\n"
            "FNF:2\n"
            "FNH:2\n"
            "DA:1,1\n"
            "DA:2,1\n"
            "LF:10\n"
            "LH:10\n"
            "end_of_record\n"
            "SF:/work/crates/core/src/low.rs\n"
            "FNF:2\n"
            "FNH:1\n"
            "DA:10,1\n"
            "DA:11,1\n"
            "DA:12,0\n"
            "DA:13,0\n"
            "LF:10\n"
            "LH:2\n"
            "end_of_record\n"
            "SF:/work/crates/core/src/none.rs\n"
            "FNF:1\n"
            "FNH:0\n"
            "DA:20,0\n"
            "DA:21,0\n"
            "DA:22,0\n"
            "DA:23,0\n"
            "LF:4\n"
            "LH:0\n"
            "end_of_record"
        )

        digest = module.render_digest(
            lcov_path=lcov,
            label="Core",
            recipients="@bandzalkin",
            run_url="https://example.test/run",
            result="success",
        )

        assert "📊 Weekly Core coverage" in digest
        assert "| Lines | 🟠 **50.00%** · 12/24 covered · 12 missing |" in digest
        assert "| Functions | 🟠 **60.00%** · 3/5 covered · 2 missing |" in digest
        assert "| Files at target | 🔴 **1/3** at or above 80% |" in digest
        assert (
            "`crates/core/src/none.rs` | 🔴 **0.00%** · 4 lines missing<br>Lines `20-23`"
            in digest
        )
        assert (
            "`crates/core/src/low.rs` | 🔴 **20.00%** · 8 lines missing<br>Lines `12-13`"
            in digest
        )
        assert "covered.rs" not in digest
        assert module.format_line_ranges((1, 3, 5, 7, 9, 11, 13, 15, 17)) == (
            "1, 3, 5, 7, 9, 11, 13, 15, … (1 more ranges)"
        )
