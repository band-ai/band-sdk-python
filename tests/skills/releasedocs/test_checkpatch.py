"""The docs edit check stops path breaches and marks content breaches do-not-merge."""

from __future__ import annotations

import json
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
# Keeps the existing import, JSX tag, and allowed link, and adds an allowed
# link, an allowed autolink, a relative link, and a snippet whose code
# imports, exports, links, and escapes a backtick.
ALLOWED_EDIT = DOCS_PAGE_TEXT.replace(
    "Create an agent with `Agent.create()`.",
    "Create an agent with `Agent.start({})`; see https://github.com/band-ai/band-sdk-python,"
    " <https://docs.band.ai/sdks>, and the [overview](../overview#agents).\n\n"
    '```python\nimport asyncio\nURL = "http://localhost:4000"\n```\n\n'
    "```bash\nexport BAND_API_KEY=key\necho \\`date\\`\n```",
)


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
        ],
        ids=["outside-link", "escaped-backtick", "wrapped-code-span"],
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
        ("edit", "kinds"),
        [
            (DOCS_PAGE_TEXT + 'import { Secret } from "@x";\n', ["IMPORT"]),
            (DOCS_PAGE_TEXT + "<Callout>Run it.</Callout>\n", ["JSX"]),
            (DOCS_PAGE_TEXT.replace("<Tabs>", "<Tabs onClick={go}>"), ["JSX"]),
            (DOCS_PAGE_TEXT + '{globalThis.fetch("/x")}\n', ["JSX"]),
            (
                DOCS_PAGE_TEXT + "\\`<Evil src={x} />\\`\n",
                ["JSX", "ESCAPED_BACKTICK"],
            ),
            (
                DOCS_PAGE_TEXT + "\\`[x](https://evil.example)\\`\n",
                ["LINK", "ESCAPED_BACKTICK"],
            ),
            (
                DOCS_PAGE_TEXT
                + "Use `C:\\` then [docs](https://evil.example) and `x`.\n",
                ["ESCAPED_BACKTICK"],
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
            (DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/x/..)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[docs]: http://evil.example\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[the guide](\n//evil.example/x)\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "[g]:\n//evil.example/x\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "- [a]: //evil.example\n\nSee [docs][a].\n", ["LINK"]),
            (DOCS_PAGE_TEXT + "> [a]: //evil.example\n\nSee [docs][a].\n", ["LINK"]),
            (
                DOCS_PAGE_TEXT + "[a\\]b]: //evil.example\n\nSee [docs][a\\]b].\n",
                ["LINK"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n***\n[x](//evil.example) two` end.\n",
                ["WRAPPED_CODE"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n---\n[x](//evil.example) two` end.\n",
                ["WRAPPED_CODE"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n> [x](//evil.example) two` end.\n",
                ["WRAPPED_CODE"],
            ),
            (
                DOCS_PAGE_TEXT + "Intro `one\n___\n<Danger /> two` end.\n",
                ["WRAPPED_CODE"],
            ),
        ],
        ids=[
            "import",
            "jsx-tag",
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
            "trailing-path-escape",
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
