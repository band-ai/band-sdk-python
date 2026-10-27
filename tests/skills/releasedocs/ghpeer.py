"""Peer: a ``gh`` double holding Fern's PRs and check runs; any other command fails the test."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from tests.skills.releasedocs.fernremote import FernRemote

# gh pr list returns at most this many files per PR.
GH_FILES_PER_PR = 100
PULL_FILES = re.compile(r"^repos/[\w-]+/[\w-]+/pulls/(\d+)/files$")
CHECK_RUNS = re.compile(r"^repos/[\w-]+/[\w-]+/commits/(\w+)/check-runs\?per_page=100$")
MERGE = re.compile(r"^repos/[\w-]+/[\w-]+/pulls/(\d+)/merge$")
# GitHub's PR states, as gh reports them.
OPEN, CLOSED, MERGED = "OPEN", "CLOSED", "MERGED"


def check_run(status: str, conclusion: str | None) -> dict[str, str | None]:
    return {"name": "run", "status": status, "conclusion": conclusion}


PASSING = [check_run("completed", "success"), check_run("completed", "success")]
FAILING = [check_run("completed", "success"), check_run("completed", "failure")]
PENDING = [check_run("completed", "success"), check_run("in_progress", None)]
# Fern's second check workflow has not registered its run yet.
PARTIAL = [check_run("completed", "success")]


@dataclass
class FakePr:
    number: int
    head: str
    state: str
    sha: str
    repo: str
    body: str = ""
    files: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    earlier_heads: list[str] = field(default_factory=list)

    def move_head(self, sha: str) -> None:
        """Point the PR at a new head; its earlier heads keep their check runs."""
        self.earlier_heads.append(self.sha)
        self.sha = sha

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/pull/{self.number}"


class FakeGh:
    """Answers the ``gh`` commands the filing scripts send, from in-memory PRs.

    A created PR's head comes from the local remote, as GitHub reads it from the
    pushed branch. Check runs are scripted per branch; an unscripted branch has
    none yet, so its checks are still pending. A close of a PR in
    ``failing_closes`` fails once, as a GitHub error would.
    """

    def __init__(self, repo: str, base: str, remote: FernRemote | None = None) -> None:
        self.repo = repo
        self.base = base
        self.remote = remote
        self.prs: list[FakePr] = []
        self.checks: dict[str, list[dict[str, str | None]]] = {}
        self.failing_closes: set[int] = set()

    def add_pr(
        self,
        head: str,
        state: str,
        *,
        sha: str = "0" * 40,
        body: str = "",
        files: list[str] | None = None,
    ) -> FakePr:
        pr = FakePr(len(self.prs) + 1, head, state, sha, self.repo, body, files or [])
        self.prs.append(pr)
        return pr

    def on(self, head: str) -> list[FakePr]:
        return [pr for pr in self.prs if pr.head == head]

    def number(self, value: str) -> FakePr:
        (pr,) = [pr for pr in self.prs if pr.number == int(value)]
        return pr

    def __call__(self, *args: str) -> str:
        match args:
            case (
                "pr",
                "list",
                "--repo",
                self.repo,
                "--head",
                head,
                "--state",
                "all",
                "--json",
                "number,state,url",
            ):
                return json.dumps(
                    [
                        {"number": p.number, "state": p.state, "url": p.url}
                        for p in self.on(head)
                    ]
                )
            case (
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                "open",
                "--limit",
                _,
                "--json",
                "number,url,headRefName,changedFiles,files",
            ):
                return json.dumps([self.listed(p) for p in self.prs if p.state == OPEN])
            case ("api", path, "--paginate", "--jq", ".[].filename") if (
                match := PULL_FILES.match(path)
            ):
                return "".join(f"{name}\n" for name in self.number(match[1]).files)
            case (
                "pr",
                "list",
                "--repo",
                self.repo,
                "--state",
                state,
                "--search",
                search,
                "--limit",
                _,
                "--json",
                "number,headRefName",
            ):
                prefix = search.removeprefix("head:")
                found = [
                    p
                    for p in self.prs
                    if p.state == state.upper() and p.head.startswith(prefix)
                ]
                return json.dumps(
                    [{"number": p.number, "headRefName": p.head} for p in found]
                )
            case (
                "pr",
                "create",
                "--repo",
                self.repo,
                "--base",
                self.base,
                "--head",
                head,
                "--title",
                _,
                "--body-file",
                body,
            ):
                assert self.remote is not None, "this test has no remote to push to"
                sha = self.remote.head(head)
                pr = self.add_pr(
                    head, OPEN, sha=sha, body=Path(body).read_text("utf-8")
                )
                return pr.url + "\n"
            case (
                "pr",
                "edit",
                number,
                "--repo",
                self.repo,
                "--title",
                _,
                "--body-file",
                body,
            ):
                assert self.remote is not None, "this test has no remote to push to"
                pr = self.number(number)
                pr.move_head(self.remote.head(pr.head))
                pr.body = Path(body).read_text("utf-8")
                return ""
            case (
                "pr",
                "view",
                number,
                "--repo",
                self.repo,
                "--json",
                "body",
                "--jq",
                ".body",
            ):
                return self.number(number).body + "\n"
            case ("api", path) if match := CHECK_RUNS.match(path):
                (pr,) = [p for p in self.prs if match[1] in (p.sha, *p.earlier_heads)]
                return json.dumps({"check_runs": self.checks.get(pr.head, [])})
            case ("api", "-X", "PUT", path, "-f", "merge_method=squash", "-f", sha) if (
                match := MERGE.match(path)
            ):
                pr = self.number(match[1])
                if pr.state != OPEN or sha != f"sha={pr.sha}":
                    raise RuntimeError(
                        "gh api failed: the PR is not mergeable at that head"
                    )
                pr.state = MERGED
                return "{}"
            case ("pr", "close", number, "--repo", self.repo, "--delete-branch"):
                self.number(number).state = CLOSED
                return ""
            case ("pr", "close", number, "--repo", self.repo, "--comment", comment):
                pr = self.number(number)
                if pr.number in self.failing_closes:
                    self.failing_closes.remove(pr.number)
                    raise RuntimeError("gh pr close failed")
                pr.state = CLOSED
                pr.comments.append(comment)
                return ""
        raise AssertionError(f"unsupported gh command: {args}")

    @staticmethod
    def listed(pr: FakePr) -> dict[str, object]:
        files = [{"path": name} for name in pr.files[:GH_FILES_PER_PR]]
        return {
            "number": pr.number,
            "url": pr.url,
            "headRefName": pr.head,
            "changedFiles": len(pr.files),
            "files": files,
        }
