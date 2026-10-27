"""The docs edit check stops path breaches and marks content breaches do-not-merge."""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.fernremote import write_tree
from tests.skills.releasedocs.outputsfile import read_outputs
from tests.skills.releasedocs.samples import DOCS_PAGE, DOCS_PAGE_TEXT

PAGES = "fern/docs/pages/integrations/sdks"
OTHER_PAGE = f"{PAGES}/overview.mdx"
OUTSIDE = "fern/docs/pages/platform/rooms.mdx"
# A file among the pages that is not a page.
PAGES_META = f"{PAGES}/_meta.json"
PRISTINE = {
    DOCS_PAGE: DOCS_PAGE_TEXT,
    OTHER_PAGE: "# Overview\n",
    OUTSIDE: "# Rooms\n",
    PAGES_META: "{}\n",
}
# Keeps the frontmatter and the existing import, JSX tag, and allowed link,
# and adds an allowed bare URL, a relative link, an allowed reference
# definition, and a snippet whose code imports, exports, links, and escapes a
# backtick.
ALLOWED_EDIT = DOCS_PAGE_TEXT.replace(
    "Create an agent with `Agent.create()`.",
    "Create an agent with `Agent.start({})`; see https://github.com/band-ai/band-sdk-python"
    " and the [overview](../overview#agents).\n\n"
    '```python\nimport asyncio\nURL = "http://localhost:4000"\n```\n\n'
    "```bash\nexport BAND_API_KEY=key\necho \\`date\\`\n```\n\n"
    "[ref]: https://docs.band.ai/sdks",
)
CONSTRUCTS = (
    'import { Note } from "@components";',
    "<Note>Read this.</Note>",
    "{props.version}",
)
# Page text that must never reach the logs.
PRIVATE = "private-page-text"
# MDX reads `<` as JSX, so it cannot parse a page holding an autolink.
AUTOLINK = "<https://docs.band.ai/sdks>"
# Keeps node from ever exiting. NODE_OPTIONS splits on spaces, so it has none.
STALL = "setInterval(()=>{},1e9)"
# An escaped YAML string Fern renders as the page's edit link.
ESCAPED_EDIT_LINK = 'edit-this-page-url: "\\x68ttps://evil.example/x"\n'


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    pristine = write_tree(tmp_path / "pristine", PRISTINE)
    edited = tmp_path / "edited"
    shutil.copytree(pristine, edited)
    return pristine, edited


def run(checkpatch: ModuleType, trees: tuple[Path, Path], tmp_path: Path) -> int:
    pristine, edited = trees
    argv = [str(pristine), str(edited), "--out", str(tmp_path / "patch.json")]
    return checkpatch.main([*argv, "--outputs", str(tmp_path / "outputs")])


def before_the_reader(script: str) -> str:
    """Return ``NODE_OPTIONS`` that make node run ``script`` before the reader."""
    return f"--import=data:text/javascript,{script}"


def make_symlink(pristine: Path, edited: Path) -> None:
    page = edited / DOCS_PAGE
    page.unlink()
    try:
        page.symlink_to(edited / OTHER_PAGE)
    except OSError:
        pytest.skip("this account cannot create symlinks")


class TestCheckPatch:
    def test_allowed_edit_is_filed_and_mergeable(
        self, checkpatch: ModuleType, trees: tuple[Path, Path], tmp_path: Path
    ) -> None:
        write_tree(trees[1], {DOCS_PAGE: ALLOWED_EDIT})
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs") == {
            "changed": "1",
            "mergeable": "true",
        }
        assert json.loads((tmp_path / "patch.json").read_text("utf-8")) == {
            "files": [DOCS_PAGE],
            "content_violations": [],
        }

    @pytest.mark.parametrize(
        ("breach", "kinds"),
        [
            (
                lambda _, edited: write_tree(edited, {OUTSIDE: "# Rooms, edited\n"}),
                {"OUTSIDE"},
            ),
            (
                lambda _, edited: write_tree(edited, {PAGES_META: '{"x": 1}\n'}),
                {"OUTSIDE"},
            ),
            (
                lambda _, edited: write_tree(edited, {f"{PAGES}/new.mdx": "x"}),
                {"CREATED"},
            ),
            (lambda _, edited: (edited / OTHER_PAGE).unlink(), {"DELETED"}),
            (
                lambda _, edited: (edited / OTHER_PAGE).rename(
                    edited / f"{PAGES}/moved.mdx"
                ),
                {"CREATED", "DELETED"},
            ),
            (make_symlink, {"SYMLINK"}),
            (
                lambda _, edited: write_tree(
                    edited, {OTHER_PAGE: b"\xff\xfe overview"}
                ),
                {"NOT_UTF8"},
            ),
        ],
        ids=[
            "outside",
            "non-page-beside-the-pages",
            "new-file",
            "deletion",
            "rename",
            "symlink",
            "not-utf8",
        ],
    )
    def test_path_breach_stops_the_filing(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        breach: Callable[[Path, Path], object],
        kinds: set[str],
    ) -> None:
        breach(*trees)
        with pytest.raises(checkpatch.PatchError) as raised:
            checkpatch.check_patch(*trees)
        assert {finding.kind for finding in raised.value.findings} == {
            checkpatch.PathViolation[kind] for kind in kinds
        }
        assert run(checkpatch, trees, tmp_path) == 1
        assert (
            read_outputs(tmp_path / "outputs"),
            (tmp_path / "patch.json").exists(),
        ) == ({}, False)

    def test_pages_up_to_the_cap_are_filed(
        self,
        checkpatch: ModuleType,
        releasedata: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
    ) -> None:
        pages = [f"{PAGES}/p{n}.mdx" for n in range(releasedata.MAX_REVIEWED_PAGES)]
        write_tree(trees[0], dict.fromkeys(pages, "before\n"))
        write_tree(trees[1], dict.fromkeys(pages, "after\n"))
        assert run(checkpatch, trees, tmp_path) == 0

    def test_more_pages_than_the_cap_stops_the_filing(
        self,
        checkpatch: ModuleType,
        releasedata: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
    ) -> None:
        pages = [f"{PAGES}/p{n}.mdx" for n in range(releasedata.MAX_REVIEWED_PAGES + 1)]
        write_tree(trees[0], dict.fromkeys(pages, "before\n"))
        write_tree(trees[1], dict.fromkeys(pages, "after\n"))
        with pytest.raises(checkpatch.PatchError) as raised:
            checkpatch.check_patch(*trees)
        assert [finding.kind for finding in raised.value.findings] == [
            checkpatch.PathViolation.TOO_MANY
        ]
        assert run(checkpatch, trees, tmp_path) == 1

    @pytest.mark.parametrize(
        ("extra", "violations"),
        [(0, []), (1, ["TOO_LONG"])],
        ids=["at-the-cap", "past-the-cap"],
    )
    @pytest.mark.parametrize(
        "pages", [[DOCS_PAGE], [DOCS_PAGE, OTHER_PAGE]], ids=["one-page", "two-pages"]
    )
    def test_changed_lines_are_capped_across_pages(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        pages: list[str],
        extra: int,
        violations: list[str],
    ) -> None:
        lines = [f"Line {n}.\n" for n in range(checkpatch.MAX_CHANGED_LINES + extra)]
        write_tree(
            trees[1],
            {
                page: PRISTINE[page] + "".join(lines[index :: len(pages)])
                for index, page in enumerate(pages)
            },
        )
        assert run(checkpatch, trees, tmp_path) == 0
        check = json.loads((tmp_path / "patch.json").read_text("utf-8"))
        assert [finding["kind"] for finding in check["content_violations"]] == [
            checkpatch.ContentViolation[kind] for kind in violations
        ]

    @pytest.mark.parametrize(
        "existing",
        [
            "[LangGraph](https://langchain-ai.github.io/langgraph/) runs graphs.",
            "Type \\`x\\` here.",
            "Wrapped `code\nspan` here.",
            "- [a]: https://docs.band.ai/sdks",
        ],
        ids=[
            "outside-link",
            "escaped-backtick",
            "wrapped-code-span",
            "definition-in-a-list-item",
        ],
    )
    def test_editing_beside_an_existing_construct_stays_mergeable(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        existing: str,
    ) -> None:
        write_tree(trees[0], {OTHER_PAGE: f"# Overview\n\n{existing}\n\nMore.\n"})
        write_tree(
            trees[1], {OTHER_PAGE: f"# Overview\n\n{existing}\n\nMore, edited.\n"}
        )
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs")["mergeable"] == "true"

    @pytest.mark.parametrize(
        "added",
        [
            "Use `C:\\` for the path.",
            "Wrapped `code\nspan` here.",
            "Type \\`x\\` here.",
            "- [a]: https://docs.band.ai/sdks\n\nSee [the docs][a].",
            "- Run it:\n\n  ```bash\n  open [x](//evil.example)\n  ```",
            "[repo](https://github.com/band-ai/x/../band-sdk-python)",
            "[repo](https://github.com/band-ai/./band-sdk-python)",
            "[org](https://github.com/band-ai/.)",
        ],
        ids=[
            "code-span-ending-in-a-backslash",
            "wrapped-code-span",
            "escaped-backticks",
            "definition-in-a-list-item",
            "fence-in-a-list-item",
            "dot-segment-inside-an-allowed-path",
            "single-dot-segment-inside-an-allowed-path",
            "trailing-single-dot-segment",
        ],
    )
    def test_adding_code_or_an_allowed_link_stays_mergeable(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        added: str,
    ) -> None:
        write_tree(trees[1], {OTHER_PAGE: f"# Overview\n\n{added}\n"})
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs")["mergeable"] == "true"

    @pytest.mark.parametrize(
        "removed", CONSTRUCTS, ids=["import", "jsx-tag", "mdx-expression"]
    )
    def test_removing_a_construct_stays_mergeable(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        removed: str,
    ) -> None:
        kept = [construct for construct in CONSTRUCTS if construct != removed]
        write_tree(trees[0], {OTHER_PAGE: "\n\n".join(CONSTRUCTS) + "\n"})
        write_tree(trees[1], {OTHER_PAGE: "\n\n".join(kept) + "\n"})
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs")["mergeable"] == "true"

    def test_editing_a_page_mdx_could_not_parse_files_the_pr_unmergeable(
        self, checkpatch: ModuleType, trees: tuple[Path, Path], tmp_path: Path
    ) -> None:
        write_tree(trees[0], {OTHER_PAGE: f"# Overview\n\n{AUTOLINK}\n"})
        write_tree(trees[1], {OTHER_PAGE: "# Overview\n\nhttps://docs.band.ai/sdks\n"})
        assert run(checkpatch, trees, tmp_path) == 0
        assert json.loads((tmp_path / "patch.json").read_text("utf-8")) == {
            "files": [OTHER_PAGE],
            "content_violations": [
                {"path": OTHER_PAGE, "kind": checkpatch.ContentViolation.UNPARSED}
            ],
        }

    @pytest.mark.parametrize(
        ("variable", "value"),
        [
            ("PATH", os.devnull),
            (
                "NODE_OPTIONS",
                before_the_reader(f"process.stderr.write('{PRIVATE}');process.exit(3)"),
            ),
            ("NODE_OPTIONS", before_the_reader("process.stdout.write('x')")),
        ],
        ids=["node-missing", "reader-exits-non-zero", "reader-writes-malformed-output"],
    )
    def test_reader_failure_stops_the_filing_without_logging_pages(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        capfd: pytest.CaptureFixture[str],
        variable: str,
        value: str,
    ) -> None:
        write_tree(trees[0], {OTHER_PAGE: f"---\ntitle: {PRIVATE}\n---\n\nOne.\n"})
        write_tree(trees[1], {OTHER_PAGE: f"---\ntitle: {PRIVATE}\n---\n\nTwo.\n"})
        monkeypatch.setenv(variable, value)
        assert run(checkpatch, trees, tmp_path) == 1
        assert (
            read_outputs(tmp_path / "outputs"),
            (tmp_path / "patch.json").exists(),
        ) == ({}, False)
        assert [record.levelno for record in caplog.records] == [logging.ERROR]
        assert PRIVATE not in caplog.text
        assert PRIVATE not in "".join(capfd.readouterr())

    def test_a_stalled_reader_fails_without_quoting_pages(
        self, checkpatch: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NODE_OPTIONS", before_the_reader(STALL))
        with pytest.raises(checkpatch.ReaderError) as raised:
            checkpatch.read_pages([PRIVATE], timeout=0)
        assert PRIVATE not in str(raised.value)

    def test_no_edits_need_no_reader(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PATH", os.devnull)
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs") == {
            "changed": "0",
            "mergeable": "true",
        }

    @pytest.mark.parametrize(
        ("edit", "kinds"),
        [
            (DOCS_PAGE_TEXT + '\nimport { Secret } from "@x";\n', ["IMPORT"]),
            (DOCS_PAGE_TEXT + "<Callout>Run it.</Callout>\n", ["JSX"]),
            (DOCS_PAGE_TEXT + "<Tabs>\nMore.\n</Tabs>\n", ["JSX"]),
            (DOCS_PAGE_TEXT.replace("<Tabs>", "<Tabs onClick={go}>"), ["JSX"]),
            (DOCS_PAGE_TEXT + '{globalThis.fetch("/x")}\n', ["JSX"]),
            (
                DOCS_PAGE_TEXT + "\\`<Evil src={x} />\\`\n",
                ["JSX"],
            ),
            (
                DOCS_PAGE_TEXT + "\\`[x](https://evil.example)\\`\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT
                + "Use `C:\\` then [docs](https://evil.example) and `x`.\n",
                ["LINK"],
            ),
            (DOCS_PAGE_TEXT + "See https://band.ai.example.com/x.\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "See www.example.com.\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[docs](javascript:alert(1))\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[docs](//evil.example/x)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[d](&#104;ttps://evil.example)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[d](&#47;&#47;evil.example)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[d](https&#58;//evil.example)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[d](https\\://evil.example)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/../evil)\n", ["LINK"]),
            (
                DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/%2E%2e/evil)\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/./../evil)\n",
                ["LINK"],
            ),
            (DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/..)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/..?x)\n", ["LINK"]),
            (
                DOCS_PAGE_TEXT + "[pkg](https://pypi.org/project/band-sdk/..#a)\n",
                ["LINK"],
            ),
            (DOCS_PAGE_TEXT + "\n[d]: http://evil.example\n\nSee [d].\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[the guide](\n//evil.example/x)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "\n[g]:\n//evil.example/x\n\nSee [g].\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "- [a]: //evil.example\n\nSee [docs][a].\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "> [a]: //evil.example\n\nSee [docs][a].\n", ["LINK"]),
            (
                DOCS_PAGE_TEXT + "\n[a\\]b]: //evil.example\n\nSee [docs][a\\]b].\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n***\n[x](//evil.example) two` end.\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n---\n[x](//evil.example) two` end.\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n> [x](//evil.example) two` end.\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n___\n<Danger /> two` end.\n",
                ["JSX"],
            ),
            (
                DOCS_PAGE_TEXT
                + "- item\n  ```\n  code\n[x](https://evil.example/)\n    ```\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n    ***\n[x](//evil.example) two` end.\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro\n    ```\n```\n[x](//evil.example)\n```\n",
                ["LINK"],
            ),
            (DOCS_PAGE_TEXT + "![logo](//evil.example/logo.png)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[x](java&#9;script:alert(1))\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[x](/\\evil.example)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[x](https://evil.example//docs.band.ai/x)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[x](https://[evil.example/x)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + f"{AUTOLINK}\n", ["UNPARSED"]),
            (
                DOCS_PAGE_TEXT.replace("---\n\n", ESCAPED_EDIT_LINK + "---\n\n"),
                ["FRONTMATTER"],
            ),
            (
                DOCS_PAGE_TEXT.replace(
                    "---\n\n", 'logo:\n  light: "//evil.example/l.png"\n---\n\n'
                ),
                ["FRONTMATTER"],
            ),
            (
                DOCS_PAGE_TEXT.replace("---\n\n", ESCAPED_EDIT_LINK + "---x\n\n"),
                ["UNPARSED"],
            ),
        ],
        ids=[
            "import",
            "jsx-tag",
            "another-copy-of-an-existing-jsx-tag",
            "jsx-attribute",
            "mdx-expression",
            "jsx-in-escaped-backticks",
            "link-in-escaped-backticks",
            "backslash-closing-a-code-span",
            "foreign-link",
            "bare-www-link",
            "script-link",
            "protocol-relative-link",
            "entity-encoded-scheme",
            "entity-encoded-protocol-relative-link",
            "entity-encoded-colon",
            "backslash-escaped-colon",
            "path-escaping-link",
            "percent-encoded-path-escape",
            "single-dot-before-a-path-escape",
            "trailing-path-escape",
            "path-escape-before-a-query",
            "path-escape-before-a-fragment",
            "reference-definition",
            "link-split-across-lines",
            "reference-split-across-lines",
            "definition-in-a-list-item",
            "definition-in-a-blockquote",
            "definition-with-an-escaped-bracket",
            "code-span-across-a-thematic-break",
            "code-span-across-a-setext-underline",
            "code-span-into-a-blockquote",
            "jsx-in-a-code-span-across-a-break",
            "list-item-fence-hiding-a-link",
            "code-span-across-an-indented-thematic-break",
            "indented-fence-closer-after-a-paragraph",
            "foreign-image",
            "tab-inside-a-scheme",
            "backslash-read-as-a-slash",
            "allowed-site-in-a-foreign-path",
            "malformed-host",
            "not-mdx",
            "frontmatter-edit-link",
            "frontmatter-logo",
            "frontmatter-micromark-does-not-close",
        ],
    )
    def test_content_breach_files_the_pr_unmergeable(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        edit: str,
        kinds: list[str],
    ) -> None:
        write_tree(trees[1], {DOCS_PAGE: edit})
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs") == {
            "changed": "1",
            "mergeable": "false",
        }
        check = json.loads((tmp_path / "patch.json").read_text("utf-8"))
        assert [finding["kind"] for finding in check["content_violations"]] == [
            checkpatch.ContentViolation[kind] for kind in kinds
        ]
