from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from remote_agent.band_format import format_source
from tests.contracts.runtime.formatter import (
    FORMATTED,
    MESSY,
    PROMPTS_COMPLETE,
    PROMPTS_PARTIAL,
    PROMPTS_PARTIAL_MISSING,
    ROOT,
    BandFormatCLIFixture,
    run_cli,
    vector_inputs,
)


class TestBandFormatCLI(BandFormatCLIFixture):
    def test_check_exits_1_and_lists_unformatted_files_without_writing(self) -> None:
        result = run_cli("--check", str(self.messy), str(self.tidy))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": {"code": "unformatted", "files": [str(self.messy)]}},
        )
        self.assertEqual(self.messy.read_text(), MESSY)

    def test_check_passes_on_formatted_files(self) -> None:
        result = run_cli("--check", str(self.tidy))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(json.loads(result.stdout), {"checked": 1, "unformatted": []})

    def test_rewrites_files_in_place_and_directories_recursively(self) -> None:
        nested = self.dir / "deps" / "skip.band"
        nested.parent.mkdir()
        nested.write_text(MESSY)
        result = run_cli(str(self.dir))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            json.loads(result.stdout), {"formatted": [str(self.messy)], "unchanged": 1}
        )
        self.assertEqual(self.messy.read_text(), FORMATTED)
        self.assertEqual(nested.read_text(), MESSY)

    def test_check_passes_every_expected_vector_and_flags_every_input_vector(
        self,
    ) -> None:
        expected = sorted(str(p) for p in vector_inputs("expected"))
        inputs = sorted(str(p) for p in vector_inputs())
        self.assertTrue(any(p.endswith(".md") for p in inputs))
        result = run_cli("--check", *expected)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            json.loads(result.stdout), {"checked": len(expected), "unformatted": []}
        )
        result = run_cli("--check", *inputs)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": {"code": "unformatted", "files": inputs}},
        )

    @pytest.mark.contract_posix
    def test_a_directory_walk_takes_regular_files_only(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="bandfmt-py-outside-"))
        self.addCleanup(lambda: shutil.rmtree(outside))
        (outside / "z.band").write_text(MESSY)
        for skipped in ("deps", "_build", "node_modules", ".venv"):
            (self.dir / skipped).mkdir()
            (self.dir / skipped / "x.band").write_text(MESSY)
        (self.dir / "linked").symlink_to(outside)
        (self.dir / "alias.band").symlink_to(outside / "z.band")
        (self.dir / "null.band").symlink_to("/dev/null")
        (self.dir / "zero.band").symlink_to("/dev/zero")
        self.fifo("fifo.band")
        result = run_cli("--check", str(self.dir), timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": {"code": "unformatted", "files": [str(self.messy)]}},
        )

    @pytest.mark.contract_posix
    def test_refuses_a_fifo_symlink_or_device_named_on_the_command_line_without_reading(
        self,
    ) -> None:
        link = self.dir / "link.band"
        link.symlink_to(self.messy)
        for path in (self.fifo("fifo.band"), link, Path("/dev/zero")):
            with self.subTest(path=str(path)):
                result = run_cli("--check", str(path), timeout=15)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    json.loads(result.stderr),
                    {"error": {"code": "unsafe_contract_path", "path": str(path)}},
                )
        self.assertEqual(self.messy.read_text(), MESSY)

    def test_refuses_a_file_over_the_compiler_source_limit(self) -> None:
        big = self.dir / "big.band"
        big.write_bytes(b"contract @1h => expired\n" + b"%" * 266_240)
        result = run_cli("--check", str(big))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": {"code": "source_size", "path": str(big)}},
        )

    def test_reports_a_missing_file_as_a_file_io_error(self) -> None:
        missing = self.dir / "missing.band"
        result = run_cli("--check", str(missing))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr),
            {"error": {"code": "file_io", "detail": ["enoent"], "path": str(missing)}},
        )

    def test_a_directory_walk_skips_what_band_format_ignore_lists_also_in_an_ancestor(
        self,
    ) -> None:
        sub = self.dir / "sub"
        (sub / "vectors" / "one").mkdir(parents=True)
        pinned, keep, vector = (
            sub / "pinned.band",
            sub / "keep.band",
            sub / "vectors" / "one" / "input.band",
        )
        for path in (pinned, keep, vector):
            path.write_text(MESSY)
        (self.dir / ".band-format-ignore").write_text(
            "# Hash-pinned copies and deliberately unformatted vectors.\n\n"
            "sub/pinned.band\n  sub/vectors/*/input.band\n"
        )
        result = run_cli("--check", str(self.dir))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr)["error"]["files"],
            sorted([str(self.messy), str(keep)]),
        )
        result = run_cli("--check", str(sub))
        self.assertEqual(json.loads(result.stderr)["error"]["files"], [str(keep)])
        result = run_cli("--check", str(pinned))
        self.assertEqual(json.loads(result.stderr)["error"]["files"], [str(pinned)])

    @pytest.mark.contract_posix
    def test_a_listed_directory_is_skipped_whole_and_only_a_regular_ignore_file_counts(
        self,
    ) -> None:
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "deep.band").write_text(MESSY)
        (self.dir / "listed.txt").write_text("messy.band\n")
        (self.dir / ".band-format-ignore").symlink_to(self.dir / "listed.txt")
        result = run_cli("--check", str(self.dir))
        self.assertEqual(
            json.loads(result.stderr)["error"]["files"],
            [str(self.messy), str(self.dir / "sub" / "deep.band")],
        )
        (self.dir / ".band-format-ignore").unlink()
        (self.dir / ".band-format-ignore").write_text("sub\n?essy.band\n")
        result = run_cli("--check", str(self.dir))
        self.assertEqual(
            (result.returncode, json.loads(result.stdout)),
            (0, {"checked": 1, "unformatted": []}),
        )

    def test_star_and_question_mark_match_within_one_path_segment(self) -> None:
        for name in ("ab", "c"):
            (self.dir / name).mkdir()
            (self.dir / name / "b.band").write_text(MESSY)
        (self.dir / ".band-format-ignore").write_text("*.band\n?\n")
        result = run_cli("--check", str(self.dir))
        self.assertEqual(
            json.loads(result.stderr)["error"]["files"],
            [str(self.dir / "ab" / "b.band")],
        )

    def test_the_repository_walk_is_clean_with_the_mix_file_set(self) -> None:
        self.assert_walk_is_clean(ROOT)

    def test_the_repository_walk_stays_clean_when_a_formatted_band_file_is_added(
        self,
    ) -> None:
        # A new formatted contract must extend the discovered file set without
        # invalidating the existing repository walk.
        tracked = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "*.band",
                "*.band-format-ignore",
            ],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        copy = self.dir / "repo"
        for name in filter(None, tracked.split(b"\0")):
            target = copy / os.fsdecode(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / os.fsdecode(name), target)
        (copy / "spec" / "tools").mkdir(parents=True, exist_ok=True)
        (copy / "spec" / "tools" / "extra-probe.band").write_text(FORMATTED)
        self.assert_walk_is_clean(copy)

    def test_formats_the_band_fences_of_markdown_files_found_by_a_walk_or_named(
        self,
    ) -> None:
        md = self.dir / "notes.md"
        fenced = "# Notes\n\nText   kept.\n\n```band\n{}```\n"
        md.write_text(fenced.format(MESSY))
        (self.dir / "plain.txt").write_text(MESSY)
        result = run_cli("--check", str(self.dir))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr)["error"]["files"],
            sorted([str(self.messy), str(md)]),
        )
        result = run_cli(str(md))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            json.loads(result.stdout), {"formatted": [str(md)], "unchanged": 0}
        )
        self.assertEqual(md.read_text(), fenced.format(FORMATTED))
        self.assertEqual(run_cli("--check", str(md)).returncode, 0)

    def test_a_markdown_file_may_be_larger_than_a_contract_up_to_4_mib(self) -> None:
        md = self.dir / "big.md"
        md.write_bytes(b"# x\n" + b"y" * 266_240 + b"\n")
        result = run_cli("--check", str(md))
        self.assertEqual(
            (result.returncode, json.loads(result.stdout)),
            (0, {"checked": 1, "unformatted": []}),
        )
        md.write_bytes(b"y" * (4_194_304 + 1))
        result = run_cli("--check", str(md))
        self.assertEqual(
            json.loads(result.stderr),
            {"error": {"code": "source_size", "path": str(md)}},
        )

    def test_require_prompts_passes_a_contract_with_every_prompt(self) -> None:
        complete = self.dir / "complete.band"
        complete.write_text(format_source(PROMPTS_COMPLETE))
        result = run_cli("--check", "--require-prompts", str(complete))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 1,
                "unformatted": [],
                "prompts": "complete",
                "prompts_skipped": [],
            },
        )

    def test_require_prompts_lists_each_missing_prompt_and_writes_nothing(self) -> None:
        # Both files are unformatted; the complete one comes first and is not written.
        complete, partial = self.dir / "a-complete.band", self.dir / "partial.band"
        complete.write_text(PROMPTS_COMPLETE)
        partial.write_text(PROMPTS_PARTIAL)
        for args in (["--check", "--require-prompts"], ["--require-prompts"]):
            with self.subTest(args=args):
                result = run_cli(*args, str(complete), str(partial))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    json.loads(result.stderr),
                    {
                        "error": {
                            "code": "prompts_required",
                            "path": str(partial),
                            "missing": PROMPTS_PARTIAL_MISSING,
                        }
                    },
                )
                self.assertEqual(complete.read_text(), PROMPTS_COMPLETE)
        # Without the flag the same files are formatted.
        result = run_cli(str(complete), str(partial))
        self.assertEqual(
            (result.returncode, json.loads(result.stdout)),
            (0, {"formatted": [str(complete), str(partial)], "unchanged": 0}),
        )

    def test_require_prompts_skips_body_only_references_and_markdown_and_says_so(
        self,
    ) -> None:
        body, md = self.dir / "body.band", self.dir / "notes.md"
        body.write_text("contract @1h => expired\n\nend done\n")
        md.write_text("```band\n" + format_source(PROMPTS_PARTIAL) + "```\n")
        result = run_cli(
            "--check", "--require-prompts", str(body), str(md), str(self.tidy)
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "prompts_required")
        self.assertEqual(json.loads(result.stderr)["error"]["path"], str(self.tidy))
        result = run_cli("--check", "--require-prompts", str(body), str(md))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            json.loads(result.stdout),
            {
                "checked": 2,
                "unformatted": [],
                "prompts": "complete",
                "prompts_skipped": [str(body), str(md)],
            },
        )

    def test_refuses_a_crlf_file_and_writes_nothing(self) -> None:
        crlf = self.dir / "crlf.band"
        crlf.write_bytes(b"contract @1h => expired\r\n")
        result = run_cli(str(crlf), str(self.messy))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stderr), {"error": {"code": "crlf", "path": str(crlf)}}
        )
        self.assertEqual(self.messy.read_text(), MESSY)
