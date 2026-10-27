# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3"]
# ///
"""Collect every PR merged in a band-sdk release range into ``release-prs.json``.

Writes the JSON to stdout. With ``--outputs`` it also appends the gate values
``proceed``, ``has_changes``, and ``reason`` for the workflow, and exits 0
when the release must not be announced (prerelease, draft, not visible, or,
live, already announced), since none of those is a failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from releasedata import SDK_TAG_PREFIX, LooseCommit, PullRequest, ReleasePrs
from stepoutputs import append_outputs

logger = logging.getLogger(__name__)

SDK_TAG_PATTERN = re.compile(rf"^{SDK_TAG_PREFIX}(\d+)\.(\d+)\.(\d+)$")
# Changes confined to these never reach SDK users.
INTERNAL_PATHS = ("tests/", ".github/", ".claude/")
# release-please labels its release PRs "autorelease: pending" / "autorelease: tagged".
AUTORELEASE_LABEL_PREFIX = "autorelease:"
# The asset whose presence marks a release as announced.
ANNOUNCED_ASSET = "release-notes.json"
# What ``gh release view`` prints for a release this token cannot see.
RELEASE_NOT_FOUND = "release not found"
RELEASE_PLEASE_CONFIG = "release-please-config.json"

PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
CONVENTIONAL_SUBJECT = re.compile(r"^(?P<type>\w+)(?:\([^)]*\))?(?P<bang>!)?:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
FIELD_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x1e"


def tag_version(tag: str) -> tuple[int, int, int] | None:
    """Parse a strict ``band-sdk-vX.Y.Z`` tag; anything else is not a release tag."""
    match = SDK_TAG_PATTERN.match(tag)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def release_version(tag: str) -> tuple[int, int, int]:
    """Parse ``tag``, refusing anything that is not a band-sdk release tag."""
    version = tag_version(tag)
    if version is None:
        raise ValueError(f"{tag!r} is not a {SDK_TAG_PREFIX}X.Y.Z tag")
    return version


class GhModel(BaseModel):
    """A ``gh --json`` object; field names are the camelCase ``gh`` keys."""

    model_config = ConfigDict(
        frozen=True, alias_generator=to_camel, validate_by_name=True, extra="ignore"
    )


class Named(GhModel):
    name: str


class Release(GhModel):
    url: str
    published_at: datetime
    is_prerelease: bool
    is_draft: bool
    assets: list[Named]

    @property
    def release_date(self) -> str:
        return self.published_at.astimezone(UTC).date().isoformat()


class PrMeta(GhModel):
    title: str
    body: str
    labels: list[Named]

    @property
    def is_release_pr(self) -> bool:
        return any(
            label.name.startswith(AUTORELEASE_LABEL_PREFIX) for label in self.labels
        )


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str
    files: list[str]

    @property
    def pr_number(self) -> int | None:
        match = PR_NUMBER.search(self.subject)
        return int(match.group(1)) if match else None

    @property
    def type(self) -> str | None:
        match = CONVENTIONAL_SUBJECT.match(self.subject)
        return match.group("type") if match else None

    @property
    def breaking(self) -> bool:
        match = CONVENTIONAL_SUBJECT.match(self.subject)
        bang = bool(match and match.group("bang"))
        return bang or bool(BREAKING_FOOTER.search(self.body))

    @property
    def internal(self) -> bool:
        return all(path.startswith(INTERNAL_PATHS) for path in self.files)


class StopReason(StrEnum):
    """Why a release gets no announcement; none of these fails the run."""

    NOT_VISIBLE = "The release is not visible to this token (missing or draft)."
    DRAFT = "The release is a draft."
    PRERELEASE = "The release is a prerelease."
    ANNOUNCED = (
        f"Already announced: the release carries {ANNOUNCED_ASSET}. "
        "Delete that asset to announce again."
    )
    NO_CHANGES = "No user-facing changes in this release."


def stop_reason(release: Release | None, *, live: bool) -> StopReason | None:
    """Return why ``release`` must not be announced, or ``None`` to proceed."""
    if release is None:
        return StopReason.NOT_VISIBLE
    if release.is_draft:
        return StopReason.DRAFT
    if release.is_prerelease:
        return StopReason.PRERELEASE
    if live and any(asset.name == ANNOUNCED_ASSET for asset in release.assets):
        return StopReason.ANNOUNCED
    return None


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"git {args[0]} failed: {exc.stderr.strip()}") from exc


def earlier_sdk_tags(repo: Path, tag: str) -> dict[str, tuple[int, int, int]]:
    """Return the band-sdk release tags reachable from before ``tag``, by version."""
    merged = git(repo, "tag", "--merged", f"{tag}^", "--list", f"{SDK_TAG_PREFIX}*")
    versions = {
        name: version for name in merged.split() if (version := tag_version(name))
    }
    if not versions:
        raise ValueError(f"no {SDK_TAG_PREFIX}* release precedes {tag}")
    return versions


def previous_sdk_tag(repo: Path, tag: str) -> str:
    """Return the highest band-sdk release tag reachable from before ``tag``."""
    versions = earlier_sdk_tags(repo, tag)
    return max(versions, key=versions.__getitem__)


def excluded_paths(repo: Path, tag: str) -> list[str]:
    """Return the root package's release-please ``exclude-paths`` at ``tag``."""
    config = json.loads(git(repo, "show", f"{tag}:{RELEASE_PLEASE_CONFIG}"))
    return config["packages"]["."].get("exclude-paths", [])


def without_excluded(files: list[str], excluded: list[str]) -> list[str]:
    prefixes = tuple(f"{path.rstrip('/')}/" for path in excluded)
    return [path for path in files if not path.startswith(prefixes)]


def range_commits(
    repo: Path, start: str, tag: str, excluded: list[str]
) -> list[Commit]:
    """Return the root package's commits in ``start..tag``, oldest first."""
    pathspec = ["--", ".", *(f":(exclude){path}" for path in excluded)]
    log = git(
        repo,
        "log",
        "--reverse",
        f"--format=%H{FIELD_SEPARATOR}%s{FIELD_SEPARATOR}%b{RECORD_SEPARATOR}",
        f"{start}..{tag}",
        *pathspec,
    )
    records = [record.strip("\n") for record in log.split(RECORD_SEPARATOR)]
    commits = []
    for record in filter(None, records):
        sha, subject, body = record.split(FIELD_SEPARATOR)
        # Without renames a move lists both paths, so moving public code into
        # tests/ still touches src/.
        listing = git(repo, "show", "--name-only", "--no-renames", "--format=", sha)
        files = [name for name in listing.splitlines() if name]
        commits.append(Commit(sha, subject, body, without_excluded(files, excluded)))
    return commits


def collect(
    repo: Path,
    tag: str,
    *,
    release: Release,
    pr_meta: Callable[[int], PrMeta],
    since: str | None = None,
) -> ReleasePrs:
    start = since or previous_sdk_tag(repo, tag)
    excluded = excluded_paths(repo, tag)
    pull_requests: list[PullRequest] = []
    loose_commits: list[LooseCommit] = []
    excluded_internal: list[int] = []
    for commit in range_commits(repo, start, tag, excluded):
        number = commit.pr_number
        if number is None:
            if not commit.internal:
                loose_commits.append(
                    LooseCommit(
                        sha=commit.sha,
                        subject=commit.subject,
                        files=commit.files,
                        breaking=commit.breaking,
                    )
                )
            continue
        if commit.internal:
            excluded_internal.append(number)
            continue
        meta = pr_meta(number)
        if meta.is_release_pr:
            excluded_internal.append(number)
            continue
        pull_requests.append(
            PullRequest(
                number=number,
                sha=commit.sha,
                title=meta.title,
                body=meta.body,
                labels=[label.name for label in meta.labels],
                files=commit.files,
                type=commit.type,
                breaking=commit.breaking,
            )
        )
    return ReleasePrs(
        tag=tag,
        previous_tag=start,
        release_url=release.url,
        release_date=release.release_date,
        pull_requests=pull_requests,
        loose_commits=loose_commits,
        excluded_internal=excluded_internal,
    )


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], check=False, capture_output=True, text=True)


def gh_failed(
    args: Sequence[str], result: subprocess.CompletedProcess[str]
) -> Exception:
    return RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")


def gh_output(*args: str) -> str:
    """Run ``gh`` and return its stdout; raise on a non-zero exit."""
    result = gh(*args)
    if result.returncode != 0:
        raise gh_failed(args, result)
    return result.stdout


def gh_release(tag: str) -> Release | None:
    """The release, or ``None`` when this token cannot see it."""
    args = (
        "release",
        "view",
        tag,
        "--json",
        "url,publishedAt,isPrerelease,isDraft,assets",
    )
    result = gh(*args)
    if result.returncode == 0:
        return Release.model_validate_json(result.stdout)
    if RELEASE_NOT_FOUND in result.stderr:
        return None
    raise gh_failed(args, result)


def gh_pr_meta(number: int) -> PrMeta:
    output = gh_output("pr", "view", str(number), "--json", "title,body,labels")
    return PrMeta.model_validate_json(output)


def run(
    repo: Path,
    tag: str,
    *,
    live: bool,
    since: str | None,
    outputs: Path | None,
    release_lookup: Callable[[str], Release | None],
    pr_meta: Callable[[int], PrMeta],
) -> ReleasePrs | None:
    """Gate and collect one release, recording the gate in ``outputs``."""
    release = release_lookup(tag)
    stop = stop_reason(release, live=live)
    if release is None or stop is not None:
        logger.info("Not announcing %s: %s", tag, stop)
        append_outputs(outputs, proceed=False, has_changes=False, reason=str(stop))
        return None
    collected = collect(repo, tag, release=release, pr_meta=pr_meta, since=since)
    has_changes = collected.has_changes
    reason = "" if has_changes else StopReason.NO_CHANGES
    if not has_changes:
        logger.info("Not announcing %s: %s", tag, reason)
    append_outputs(outputs, proceed=True, has_changes=has_changes, reason=reason)
    return collected


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--since", help="start the range at this tag instead")
    parser.add_argument(
        "--live", action="store_true", help="stop if the release is already announced"
    )
    parser.add_argument("--outputs", type=Path, help="append gate values here")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    try:
        release_version(args.tag)
    except ValueError as exc:
        logger.error("error: %s", exc)
        return 2
    try:
        collected = run(
            args.repo,
            args.tag,
            live=args.live,
            since=args.since,
            outputs=args.outputs,
            release_lookup=gh_release,
            pr_meta=gh_pr_meta,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return 1
    if collected is not None:
        sys.stdout.write(collected.model_dump_json(indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
