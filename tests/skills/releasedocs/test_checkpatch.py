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
PRISTINE = {
    DOCS_PAGE: DOCS_PAGE_TEXT,
    OTHER_PAGE: "# Overview\n",
    OUTSIDE: "# Rooms\n",
}
# Keeps the existing import, JSX tag, and allowed link, and adds an allowed
# link, a relative link, and a snippet whose code imports, exports, and links.
ALLOWED_EDIT = DOCS_PAGE_TEXT.replace(
    "Create an agent with `Agent.create()`.",
    "Create an agent with `Agent.start({})`; see https://github.com/band-ai/band-sdk-python"
    " and the [overview](../overview#agents).\n\n"
    '```python\nimport asyncio\nURL = "http://localhost:4000"\n```\n\n'
    "```bash\nexport BAND_API_KEY=key\n```",
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


def edit_many_pages(pristine: Path, edited: Path) -> None:
    pages = [f"fern/docs/pages/integrations/sdks/p{n}.mdx" for n in range(11)]
    write_tree(pristine, dict.fromkeys(pages, "before\n"))
    write_tree(edited, dict.fromkeys(pages, "after\n"))


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
            (edit_many_pages, {"TOO_MANY"}),
        ],
        ids=[
            "outside",
            "new-file",
            "deletion",
            "rename",
            "symlink",
            "not-utf8",
            "too-many",
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

    def test_editing_beside_an_existing_outside_link_stays_mergeable(
        self, checkpatch: ModuleType, trees: tuple[Path, Path], tmp_path: Path
    ) -> None:
        line = "[LangGraph](https://langchain-ai.github.io/langgraph/) runs graphs"
        write_tree(trees[0], {OTHER_PAGE: f"# Overview\n\n{line}.\n"})
        write_tree(trees[1], {OTHER_PAGE: f"# Overview\n\n{line} well.\n"})
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs")["mergeable"] == "true"

    @pytest.mark.parametrize(
        ("edit", "kind"),
        [
            (DOCS_PAGE_TEXT + 'import { Secret } from "@x";\n', "IMPORT"),
            (DOCS_PAGE_TEXT + "<Callout>Run it.</Callout>\n", "JSX"),
            (DOCS_PAGE_TEXT.replace("<Tabs>", "<Tabs onClick={go}>"), "JSX"),
            (DOCS_PAGE_TEXT + '{globalThis.fetch("/x")}\n', "JSX"),
            (DOCS_PAGE_TEXT + "\\`<Evil src={x} />\\`\n", "JSX"),
            (DOCS_PAGE_TEXT + "\\`[x](https://evil.example)\\`\n", "LINK"),
            (DOCS_PAGE_TEXT + "See https://band.ai.example.com/x.\n", "LINK"),
            (DOCS_PAGE_TEXT + "See www.example.com.\n", "LINK"),
            (DOCS_PAGE_TEXT + "[docs](javascript:alert(1))\n", "LINK"),
            (DOCS_PAGE_TEXT + "[docs](//evil.example/x)\n", "LINK"),
            (DOCS_PAGE_TEXT + "[repo](https://github.com/band-ai/../evil)\n", "LINK"),
            (DOCS_PAGE_TEXT + "[docs]: http://evil.example\n", "LINK"),
            (DOCS_PAGE_TEXT + "[the guide](\n//evil.example/x)\n", "LINK"),
            (DOCS_PAGE_TEXT + "[g]:\n//evil.example/x\n", "LINK"),
            (DOCS_PAGE_TEXT + "".join(f"Line {n}.\n" for n in range(201)), "TOO_LONG"),
        ],
        ids=[
            "import",
            "jsx-tag",
            "jsx-attribute",
            "mdx-expression",
            "jsx-in-escaped-backticks",
            "link-in-escaped-backticks",
            "foreign-link",
            "bare-www-link",
            "script-link",
            "protocol-relative-link",
            "path-escaping-link",
            "reference-definition",
            "link-split-across-lines",
            "reference-split-across-lines",
            "line-cap",
        ],
    )
    def test_content_breach_files_the_pr_unmergeable(
        self,
        checkpatch: ModuleType,
        trees: tuple[Path, Path],
        tmp_path: Path,
        edit: str,
        kind: str,
    ) -> None:
        write_tree(trees[1], {DOCS_PAGE: edit})
        assert run(checkpatch, trees, tmp_path) == 0
        assert read_outputs(tmp_path / "outputs") == {
            "changed": "1",
            "mergeable": "false",
        }
        check = json.loads((tmp_path / "patch.json").read_text("utf-8"))
        assert [finding["kind"] for finding in check["content_violations"]] == [
            checkpatch.ContentViolation[kind]
        ]
