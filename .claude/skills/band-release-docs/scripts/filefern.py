# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2.12,<3", "pyyaml>=6,<7", "pydantic-settings>=2,<3"]
# ///
"""File the Fern changelog and docs PRs from checked inputs, then merge or close them.

Runs only after every check passed, holding the write token, in a fresh clone
of Fern ``dev`` (``--fern``). For each part it skips a merged branch PR, or
commits this run's checked content, force-pushes it, and opens a new PR or
updates the open one, so nothing pushed to the branch since is merged
unchecked. An open PR whose head commit the release bot did not commit was
filed or edited by hand and is left alone. It then waits until every check
run on the PR's head succeeds and squash-merges it (live); a rehearsal (test)
closes it whatever its checks say.
A docs PR whose content checks failed is opened but never merged. Once the
docs PR is merged, by this run or an earlier one, the open docs PRs its body
supersedes are closed (live). Appends each PR link to ``--outputs`` and
``--summary``, and fails when a part is left unmerged.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path

from checkpatch import PatchCheck
from collectprs import git
from docsrange import DOCS_PRS, DocsRange
from docsreport import DocsReport, pr_body, superseded
from fernstate import (
    DOCS_OPEN_FILE,
    STATE_FILE,
    Action,
    BranchPr,
    FernState,
    Gh,
    action,
)
from githubcli import gh_output
from holdbacks import (
    add_held_argument,
    held_reasons,
    load_ledger,
    refuse_held_breaking,
    with_ledger_holds,
)
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from releasedata import (
    CHANGELOG_DIR,
    FERN_BASE,
    FERN_REPO,
    HELD_ITEMS,
    ReleaseNotes,
    ReleasePrs,
)
from renderentry import merge_into
from stepoutputs import append_outputs

logger = logging.getLogger(__name__)

COMMITTER = ("band-release-bot", "release-bot@band.ai")
# Fern runs two check workflows on every PR, both named `run`.
MIN_CHECK_RUNS = 2
COMPLETED = "completed"
PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}
CHECK_POLL_SECONDS = 30
CHECK_POLL_ATTEMPTS = 40
# The review reads the released source, where a held feature still lives.
HOLDBACKS_ACTIVE = "features are held back: check that none is documented"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    CHECKS_TOKEN: SecretStr | None = None


class Mode(StrEnum):
    LIVE = "live"
    TEST = "test"


class Outcome(StrEnum):
    NOTHING = "nothing to file"
    LEFT_OPEN = "open PR left unmerged: this run has nothing to file"
    HAND_FILED = "open PR left unmerged: it was filed by hand"
    ALREADY_MERGED = "already merged"
    MERGED = "merged"
    CLOSED = "checks passed; closed unmerged (test)"
    DO_NOT_MERGE = "opened, not merged: a content check failed"
    CHECKS_FAILED = "opened, not merged: a check failed"
    CHECKS_PENDING = "opened, not merged: the checks did not finish"
    ERROR = "failed: see the job log"


SETTLED = {Outcome.NOTHING, Outcome.ALREADY_MERGED, Outcome.MERGED, Outcome.CLOSED}


class Part(StrEnum):
    CHANGELOG = "changelog"
    DOCS = "docs"


class ChecksState(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class CheckRun(BaseModel):
    name: str
    status: str
    conclusion: str | None


class CheckRuns(BaseModel):
    check_runs: list[CheckRun]


@dataclass(frozen=True)
class Poll:
    attempts: int
    wait: Callable[[], None]


DEFAULT_POLL = Poll(CHECK_POLL_ATTEMPTS, lambda: time.sleep(CHECK_POLL_SECONDS))


@dataclass(frozen=True)
class FiledPr:
    number: int
    url: str
    sha: str
    mergeable: bool


@dataclass(frozen=True)
class ChangelogInput:
    notes: ReleaseNotes
    held: list[int]
    release: ReleasePrs


@dataclass(frozen=True)
class DocsInput:
    check: PatchCheck
    report: DocsReport
    edited: Path
    pristine: Path
    supersede: list[int]
    holds_back: bool


class StaleError(RuntimeError):
    """A page the review edited changed in Fern since it was read."""


def checks_state(runs: Sequence[CheckRun]) -> ChecksState:
    if any(
        r.status == COMPLETED and r.conclusion not in PASSING_CONCLUSIONS for r in runs
    ):
        return ChecksState.FAILED
    if len(runs) < MIN_CHECK_RUNS or any(r.status != COMPLETED for r in runs):
        return ChecksState.PENDING
    return ChecksState.PASSED


@dataclass(frozen=True)
class Filer:
    gh: Gh
    fern: Path
    mode: Mode
    tag: str
    poll: Poll
    open_docs: Collection[int]
    checks: Gh | None = None

    def title(self, subject: str) -> str:
        prefix = "Rehearsal: " if self.mode is Mode.TEST else ""
        return f"{prefix}{subject} {self.tag}"

    def start_branch(self, branch: str) -> None:
        git(
            self.fern,
            "checkout",
            "--quiet",
            "--force",
            "-B",
            branch,
            f"origin/{FERN_BASE}",
        )

    def push(self, branch: str, message: str) -> str:
        name, email = COMMITTER
        git(self.fern, "config", "user.name", name)
        git(self.fern, "config", "user.email", email)
        git(self.fern, "add", "--all")
        git(self.fern, "commit", "--quiet", "--message", message)
        git(
            self.fern,
            "push",
            "--quiet",
            "--force",
            "origin",
            f"HEAD:refs/heads/{branch}",
        )
        return git(self.fern, "rev-parse", "HEAD").strip()

    def open_pr(
        self,
        branch: str,
        title: str,
        body: str,
        *,
        mergeable: bool,
        existing: BranchPr | None,
    ) -> FiledPr:
        """Push this run's commit; update ``existing`` or open a new PR."""
        sha = self.push(branch, title)
        with tempfile.TemporaryDirectory() as scratch:
            body_file = Path(scratch) / "body.md"
            body_file.write_text(body, encoding="utf-8")
            if existing is not None:
                self.gh(
                    "pr",
                    "edit",
                    str(existing.number),
                    "--repo",
                    FERN_REPO,
                    "--title",
                    title,
                    "--body-file",
                    str(body_file),
                )
                return FiledPr(existing.number, existing.url, sha, mergeable)
            url = self.gh(
                "pr",
                "create",
                "--repo",
                FERN_REPO,
                "--base",
                FERN_BASE,
                "--head",
                branch,
                "--title",
                title,
                "--body-file",
                str(body_file),
            ).strip()
        return FiledPr(int(url.rsplit("/", 1)[1]), url, sha, mergeable)

    def wait_for_checks(self, sha: str) -> ChecksState:
        state = ChecksState.PENDING
        for attempt in range(self.poll.attempts):
            if attempt:
                self.poll.wait()
            reader = self.checks if self.checks is not None else self.gh
            output = reader(
                "api", f"repos/{FERN_REPO}/commits/{sha}/check-runs?per_page=100"
            )
            state = checks_state(CheckRuns.model_validate_json(output).check_runs)
            if state is not ChecksState.PENDING:
                break
        return state

    def settle(self, pr: FiledPr) -> Outcome:
        """Merge (live) a PR once its checks pass; close (test) it whatever they say."""
        # A rehearsal closes its PRs anyway, so it still exercises the checks.
        if not pr.mergeable and self.mode is Mode.LIVE:
            return Outcome.DO_NOT_MERGE
        state = self.wait_for_checks(pr.sha)
        if self.mode is Mode.TEST:
            # An open rehearsal would block a same-day live filing of another tag.
            self.gh(
                "pr", "close", str(pr.number), "--repo", FERN_REPO, "--delete-branch"
            )
        match state:
            case ChecksState.FAILED:
                return Outcome.CHECKS_FAILED
            case ChecksState.PENDING:
                return Outcome.CHECKS_PENDING
            case ChecksState.PASSED if self.mode is Mode.TEST:
                return Outcome.CLOSED
            case ChecksState.PASSED:
                return self.merge(pr)

    def merge(self, pr: FiledPr) -> Outcome:
        self.gh(
            "api",
            "-X",
            "PUT",
            f"repos/{FERN_REPO}/pulls/{pr.number}/merge",
            "-f",
            "merge_method=squash",
            "-f",
            f"sha={pr.sha}",
        )
        return Outcome.MERGED

    def filed_by_bot(self, pr: BranchPr) -> bool:
        """Whether the release bot committed the PR's head; an amend changes the committer."""
        email = self.gh(
            "api",
            f"repos/{FERN_REPO}/commits/refs/pull/{pr.number}/head",
            "--jq",
            ".commit.committer.email",
        )
        return email.strip() == COMMITTER[1]

    def close_superseded(self, docs_pr: int) -> None:
        """Close the open PRs the merged docs PR supersedes; a re-run closes any left."""
        if self.mode is not Mode.LIVE:
            return
        body = self.gh(
            "pr",
            "view",
            str(docs_pr),
            "--repo",
            FERN_REPO,
            "--json",
            "body",
            "--jq",
            ".body",
        )
        for number in superseded(body):
            if number in self.open_docs:
                self.gh(
                    "pr",
                    "close",
                    str(number),
                    "--repo",
                    FERN_REPO,
                    "--comment",
                    f"Superseded by #{docs_pr}",
                )
                logger.info("closed #%d: superseded by #%d", number, docs_pr)

    def file_changelog(
        self, branch: str, changelog: ChangelogInput, existing: BranchPr | None
    ) -> FiledPr:
        self.start_branch(branch)
        notes = changelog.notes
        entries = load_ledger(self.fern / HELD_ITEMS)
        refuse_held_breaking(changelog.release, entries)
        # A re-run reuses the holdbacks job's indexes; the fresh ledger may hold more.
        held = with_ledger_holds(notes, changelog.held, entries)
        reasons = held_reasons(notes, held, entries)
        with tempfile.TemporaryDirectory() as scratch:
            body = Path(scratch) / "pr-body.md"
            merge_into(notes, self.fern / CHANGELOG_DIR, held, body, reasons)
            text = body.read_text(encoding="utf-8")
        return self.open_pr(
            branch,
            self.title("docs(changelog): Python SDK"),
            text,
            mergeable=True,
            existing=existing,
        )

    def file_docs(
        self, branch: str, docs: DocsInput, existing: BranchPr | None
    ) -> FiledPr:
        self.start_branch(branch)
        for path in docs.check.files:
            target = self.fern / path
            pristine = (docs.pristine / path).read_bytes()
            if not target.is_file() or target.read_bytes() != pristine:
                raise StaleError(
                    "a reviewed page changed in Fern since the review; re-run"
                )
            shutil.copyfile(docs.edited / path, target)
        violations = [str(finding) for finding in docs.check.content_violations]
        if docs.holds_back or load_ledger(self.fern / HELD_ITEMS):
            violations.append(HOLDBACKS_ACTIVE)
        body = pr_body(docs.report, self.tag, docs.supersede, violations)
        return self.open_pr(
            branch,
            self.title("docs: Python SDK"),
            body,
            mergeable=not violations,
            existing=existing,
        )

    def new_changelog(
        self,
        branch: str,
        changelog: ChangelogInput | None,
        existing: BranchPr | None,
    ) -> FiledPr | None:
        if changelog is None:
            return None
        return self.file_changelog(branch, changelog, existing)

    def new_docs(
        self, branch: str, docs: DocsInput | None, existing: BranchPr | None
    ) -> FiledPr | None:
        if docs is None or not docs.check.files:
            return None
        return self.file_docs(branch, docs, existing)


@dataclass(frozen=True)
class PartResult:
    outcome: Outcome
    url: str = ""


def ignore_merge(number: int) -> None:
    """A changelog PR has nothing to do once merged."""


def run_part(
    filer: Filer,
    pr: BranchPr | None,
    file_new: Callable[[BranchPr | None], FiledPr | None],
    on_merged: Callable[[int], None],
) -> PartResult:
    """Skip a merged PR, else file this run's content (updating an open PR); then settle it.

    ``on_merged`` runs for a merged PR, this run's or an earlier one's.
    """
    # A failure still reports the PR it leaves behind, open or merged.
    url = pr.url if pr is not None else ""
    try:
        match action(pr):
            case Action.SKIP if pr is not None:
                on_merged(pr.number)
                return PartResult(Outcome.ALREADY_MERGED, pr.url)
            case Action.REUSE if pr is not None and not filer.filed_by_bot(pr):
                # A maintainer's PR may drop items by hand; never overwrite or merge it.
                return PartResult(Outcome.HAND_FILED, pr.url)
            case Action.REUSE:
                existing = pr
            case _:
                existing = None
        filed = file_new(existing)
        if filed is None:
            # An open PR this run can't vouch for is never merged.
            if existing is not None:
                return PartResult(Outcome.LEFT_OPEN, existing.url)
            return PartResult(Outcome.NOTHING)
        url = filed.url
        outcome = filer.settle(filed)
        if outcome is Outcome.MERGED:
            on_merged(filed.number)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("error: %s", exc)
        return PartResult(Outcome.ERROR, url)
    return PartResult(outcome, url)


def file_all(
    filer: Filer,
    state: FernState,
    changelog: ChangelogInput | None,
    docs: DocsInput | None,
) -> dict[Part, PartResult]:
    return {
        Part.CHANGELOG: run_part(
            filer,
            state.changelog_pr,
            partial(filer.new_changelog, state.changelog_branch, changelog),
            ignore_merge,
        ),
        Part.DOCS: run_part(
            filer,
            state.docs_pr,
            partial(filer.new_docs, state.docs_branch, docs),
            filer.close_superseded,
        ),
    }


def record_results(
    results: dict[Part, PartResult], outputs: Path | None, summary: Path | None
) -> None:
    for part, result in results.items():
        logger.info("%s: %s %s", part, result.outcome, result.url)
    append_outputs(
        outputs,
        changelog_pr=results[Part.CHANGELOG].url,
        docs_pr=results[Part.DOCS].url,
    )
    if summary is not None:
        with summary.open("a", encoding="utf-8") as out:
            out.writelines(
                f"- Fern {part}: {result.outcome} {result.url}\n"
                for part, result in results.items()
            )


def read_changelog(args: argparse.Namespace) -> ChangelogInput | None:
    if args.notes is None:
        return None
    return ChangelogInput(
        notes=ReleaseNotes.model_validate_json(args.notes.read_bytes()),
        held=args.held,
        release=ReleasePrs.model_validate_json(args.release_prs.read_bytes()),
    )


def read_docs(args: argparse.Namespace) -> DocsInput | None:
    if args.docs_check is None:
        return None
    return DocsInput(
        check=PatchCheck.model_validate_json(args.docs_check.read_bytes()),
        report=DocsReport.model_validate_json(args.docs_report.read_bytes()),
        edited=args.edited,
        pristine=args.pristine,
        supersede=DocsRange.model_validate_json(args.docs_range.read_bytes()).supersede,
        holds_back=bool(args.held),
    )


def main(
    argv: Sequence[str] | None = None,
    gh: Gh = gh_output,
    poll: Poll = DEFAULT_POLL,
    checks: Gh | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument(
        "--fern", type=Path, required=True, help="a fresh clone of Fern dev"
    )
    parser.add_argument(
        "--state", type=Path, required=True, help="fernstate.py's --out directory"
    )
    parser.add_argument("--mode", type=Mode, choices=list(Mode), required=True)
    parser.add_argument(
        "--notes",
        type=Path,
        help="the full release-notes.json; omit to file no changelog",
    )
    parser.add_argument(
        "--release-prs", type=Path, help="the release's release-prs.json, with --notes"
    )
    add_held_argument(parser)
    parser.add_argument(
        "--docs-check", type=Path, help="checkpatch.py's output; omit to file no docs"
    )
    parser.add_argument("--docs-report", type=Path)
    parser.add_argument("--docs-range", type=Path, help="docsrange.py's output")
    parser.add_argument("--edited", type=Path, help="the reviewed Fern tree")
    parser.add_argument(
        "--pristine", type=Path, help="the Fern tree as the review read it"
    )
    parser.add_argument("--outputs", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)

    state = FernState.model_validate_json((args.state / STATE_FILE).read_bytes())
    listing = (args.state / DOCS_OPEN_FILE).read_bytes()
    open_docs = {pr.number for pr in DOCS_PRS.validate_json(listing)}
    settings = Settings()
    if checks is None and settings.CHECKS_TOKEN is not None:
        checks = partial(
            gh_output,
            env=dict(os.environ, GH_TOKEN=settings.CHECKS_TOKEN.get_secret_value()),
        )
    filer = Filer(gh, args.fern, args.mode, args.tag, poll, open_docs, checks)
    results = file_all(filer, state, read_changelog(args), read_docs(args))
    record_results(results, args.outputs, args.summary)
    return 0 if all(result.outcome in SETTLED for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
