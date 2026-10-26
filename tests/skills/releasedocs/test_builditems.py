"""Every report item must trace to the release and carry safe text before it is bundled."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.skills.releasedocs.samples import (
    PREVIOUS_TAG,
    PRODUCT,
    RELEASE_DATE,
    RELEASE_TAG,
    RELEASE_URL,
)

LOOSE_SHA = "a" * 40
RELEASE_PRS = {
    "tag": RELEASE_TAG,
    "previous_tag": PREVIOUS_TAG,
    "release_url": RELEASE_URL,
    "release_date": RELEASE_DATE,
    "pull_requests": [
        {
            "number": 101,
            "sha": "b" * 40,
            "title": "feat: retry sends",
            "body": "",
            "labels": [],
            "files": ["src/band/send.py"],
            "type": "feat",
            "breaking": False,
        }
    ],
    "loose_commits": [
        {
            "sha": LOOSE_SHA,
            "subject": "docs: readme",
            "files": ["README.md"],
            "breaking": False,
        }
    ],
    "excluded_internal": [102],
}
BREAKING_PR = RELEASE_PRS | {
    "pull_requests": [RELEASE_PRS["pull_requests"][0] | {"breaking": True}]
}
BREAKING_COMMIT = RELEASE_PRS | {
    "loose_commits": [RELEASE_PRS["loose_commits"][0] | {"breaking": True}]
}
PRODUCT_ARGS = [
    *("--product-name", PRODUCT["name"]),
    *("--product-label", PRODUCT["label"]),
    *("--product-tag", PRODUCT["tag"]),
]


def item(
    sources: list[str], sentence: str = "Sends retry with backoff."
) -> dict[str, object]:
    return {
        "category": "Added",
        "label": "Send Retries",
        "sentence": sentence,
        "sources": sources,
    }


def documented(*items: dict[str, object]) -> dict[str, object]:
    return {"status": "documented", "items": list(items), "breaking": None}


def breaking(
    title: str = "Send Renamed", markdown: str = "Send is now post."
) -> dict[str, object]:
    return documented(item(["101"])) | {
        "breaking": {"title": title, "markdown": markdown}
    }


def build(
    builditems: ModuleType,
    tmp_path: Path,
    report: dict[str, object],
    release: dict[str, object],
) -> tuple[int, Path, Path]:
    report_file, prs_file = tmp_path / "report.json", tmp_path / "release-prs.json"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    prs_file.write_text(json.dumps(release), encoding="utf-8")
    out, outputs = tmp_path / "bundle" / "release-notes.json", tmp_path / "outputs"
    code = builditems.main(
        [
            str(report_file),
            str(prs_file),
            *PRODUCT_ARGS,
            "--out",
            str(out),
            "--outputs",
            str(outputs),
        ]
    )
    return code, out, outputs


class TestBuildItems:
    @pytest.mark.parametrize(
        ("report", "release"),
        [
            (documented(item(["999"])), RELEASE_PRS),
            (documented(item([])), RELEASE_PRS),
            (documented(item(["102"])), RELEASE_PRS),
            (documented(item(["101"])), BREAKING_PR),
            (documented(item(["101"])), BREAKING_COMMIT),
            (documented(item(["101"], "Reads the ghp_ token.")), RELEASE_PRS),
            (documented(item(["101"], "See https://example.com.")), RELEASE_PRS),
            (breaking(title="See https://example.com"), BREAKING_PR),
            (breaking(markdown="Set the ghp_ token."), BREAKING_PR),
        ],
        ids=[
            "unknown-source",
            "no-source",
            "internal-source",
            "breaking-pr-without-block",
            "breaking-commit-without-block",
            "credential",
            "url",
            "url-in-breaking-title",
            "credential-in-breaking-markdown",
        ],
    )
    def test_rejected_report_writes_nothing(
        self,
        builditems: ModuleType,
        tmp_path: Path,
        report: dict[str, object],
        release: dict[str, object],
    ) -> None:
        code, out, outputs = build(builditems, tmp_path, report, release)
        assert (code, out.exists(), outputs.exists()) == (1, False, False)

    def test_traced_items_become_the_release_notes(
        self, builditems: ModuleType, releasedata: ModuleType, tmp_path: Path
    ) -> None:
        report = documented(item(["101"]), item([LOOSE_SHA], "The readme is current."))
        code, out, outputs = build(builditems, tmp_path, report, RELEASE_PRS)
        assert code == 0
        assert outputs.read_text(encoding="utf-8") == "has_entry=true\n"
        assert releasedata.ReleaseNotes.model_validate_json(out.read_text()) == (
            releasedata.ReleaseNotes(
                tag=RELEASE_TAG,
                previous_tag=PREVIOUS_TAG,
                release_url=RELEASE_URL,
                release_date=RELEASE_DATE,
                product=PRODUCT,
                items=report["items"],
                breaking=None,
            )
        )

    def test_breaking_release_bundles_its_block(
        self, builditems: ModuleType, releasedata: ModuleType, tmp_path: Path
    ) -> None:
        report = breaking()
        code, out, _ = build(builditems, tmp_path, report, BREAKING_PR)
        assert code == 0
        notes = releasedata.ReleaseNotes.model_validate_json(out.read_text())
        assert notes.breaking == releasedata.Breaking.model_validate(report["breaking"])

    def test_no_user_facing_changes_writes_no_entry(
        self, builditems: ModuleType, tmp_path: Path
    ) -> None:
        report = {"status": "no_user_facing_changes", "items": [], "breaking": None}
        code, out, outputs = build(builditems, tmp_path, report, RELEASE_PRS)
        assert (code, out.exists(), outputs.read_text(encoding="utf-8")) == (
            0,
            False,
            "has_entry=false\n",
        )

    def test_rejection_log_never_repeats_model_text(
        self,
        builditems: ModuleType,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        leaky = item([], "Reads sk-ant-s3cr3t.") | {"label": "Key sk-ant-s3cr3t"}
        code, _, _ = build(builditems, tmp_path, documented(leaky), RELEASE_PRS)
        assert code == 1
        assert "s3cr3t" not in caplog.text
