#!/usr/bin/env python
"""Changelog a published band-sdk release with a Claude Agent SDK agent.

Runs one Claude session with the private ``changelog-generator`` plugin loaded
and asks it to run the ``generate`` skill for the release. The session returns
its outcome as structured output; anything short of a reported success exits
non-zero so the workflow raises an alert.

Run from the repo root with ``PYTHONPATH=.`` (see
``.github/workflows/changelog.yml``)::

    uv run python .github/scripts/changelog-agent.py band-sdk-v4.0.0
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import subprocess

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    query,
)
from claude_agent_sdk.types import SdkPluginConfig
from pydantic import BaseModel, DirectoryPath, Field, PositiveFloat, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from band.core.defaultmodels import ANTHROPIC_MODEL
from tests.paths import REPO_ROOT

logger = logging.getLogger(__name__)

RELEASE_TAG = re.compile(r"band-sdk-v\d+\.\d+\.\d+")
SKILL = "changelog-generator:generate"


class ChangelogAgentSettings(BaseSettings):
    """The agent's settings; the Claude CLI reads ANTHROPIC_API_KEY itself."""

    model_config = SettingsConfigDict(
        env_prefix="CHANGELOG_",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # The changelog-generator plugin folder from band-ai/private-agent-skills.
    plugin_dir: DirectoryPath  # CHANGELOG_PLUGIN_DIR
    turn_timeout_s: PositiveFloat = 2400.0  # CHANGELOG_TURN_TIMEOUT_S


class ChangelogReport(BaseModel):
    """How the changelog run ended."""

    succeeded: bool = Field(
        description=(
            "True when the release is documented in a changelog PR (opened by this run, "
            "or an earlier one this run found) and no step reported ❌."
        )
    )
    changelog_pr_url: str = Field(
        default="",
        description=(
            "URL of the changelog PR documenting this release: the one this run opened, "
            "or the existing one when the release was already documented; empty if none."
        ),
    )
    output: str = Field(
        description="The skill's Output block verbatim: version, PR URL, and one ✅/❌/⏭ line per step."
    )


AGENT_INSTRUCTIONS = f"""\
You are the release changelog agent for band-ai/band-sdk-python, running unattended in CI.
Your working directory is a clone of the repository's default branch with full history and tags.

When asked to changelog a release, invoke the `{SKILL}` skill with the release tag as its
argument and follow it to the end. Never ask anyone for confirmation.

This repository also tags band-mcp releases (`band-mcp-v*`), so a bare `git describe` picks the
wrong previous tag: use the release range given in the request.

Changelog PRs are merged by a person: open the PR but skip the skill's
`gh pr merge --auto` step, and do not count the skipped auto-merge as a failure.

End by returning the skill's outcome as the structured result."""


class ChangelogRunFailed(Exception):
    """The release was not changelogged; the message says why."""


def release_tag(value: str) -> str:
    if not RELEASE_TAG.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a band-sdk-vX.Y.Z release tag"
        )
    return value


def previous_release_tag(tag: str) -> str:
    """The band-sdk release before ``tag``, skipping other components' tags."""
    completed = subprocess.run(
        [
            "git",
            "describe",
            "--tags",
            "--abbrev=0",
            "--match",
            "band-sdk-v*",
            f"{tag}^",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def changelog_request(tag: str, previous_tag: str) -> str:
    return (
        f"Changelog release {tag}: run the `{SKILL}` skill with argument `{tag}`. "
        f"Release range: `{previous_tag}..{tag}`."
    )


def agent_options(settings: ChangelogAgentSettings) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        # Pinned: the CLI's own default model pick fails under API-key auth.
        model=ANTHROPIC_MODEL,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": AGENT_INSTRUCTIONS,
        },
        # Unattended: no one is there to approve the skill's git/gh/curl calls.
        permission_mode="bypassPermissions",
        cwd=REPO_ROOT,
        # Only the plugin's skills, never whatever config sits on the runner.
        setting_sources=[],
        plugins=[SdkPluginConfig(type="local", path=str(settings.plugin_dir))],
        output_format={
            "type": "json_schema",
            "schema": ChangelogReport.model_json_schema(),
        },
    )


async def run_changelog_session(tag: str) -> ResultMessage:
    """Run the session to its end and return its final result."""
    settings = ChangelogAgentSettings()
    request = changelog_request(tag, previous_release_tag(tag))
    result: ResultMessage | None = None
    try:
        async with asyncio.timeout(settings.turn_timeout_s):
            async for message in query(prompt=request, options=agent_options(settings)):
                match message:
                    case AssistantMessage(content=blocks):
                        for block in blocks:
                            if isinstance(block, TextBlock):
                                logger.info("Agent: %s", block.text)
                    case ResultMessage():
                        result = message
    except ClaudeSDKError as error:
        raise ChangelogRunFailed(f"the Claude session errored: {error}") from error
    except TimeoutError:
        raise ChangelogRunFailed(
            f"the session ran past {settings.turn_timeout_s:.0f}s"
        ) from None
    if result is None:
        raise ChangelogRunFailed("the session ended without a result")
    return result


def published_pr_url(result: ResultMessage) -> str:
    """The changelog PR the run opened; raises ``ChangelogRunFailed`` otherwise."""
    if result.is_error:
        raise ChangelogRunFailed(f"the session failed ({result.subtype})")
    try:
        report = ChangelogReport.model_validate(result.structured_output)
    except ValidationError as error:
        raise ChangelogRunFailed(
            f"the session returned no valid report: {error}"
        ) from error
    logger.info("Changelog agent output:\n%s", report.output)
    if not report.succeeded:
        raise ChangelogRunFailed("the changelog skill reported a failure")
    if not report.changelog_pr_url:
        raise ChangelogRunFailed("the agent reported success without a changelog PR")
    return report.changelog_pr_url


async def changelog(tag: str) -> str:
    return published_pr_url(await run_changelog_session(tag))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "tag", type=release_tag, help="published release tag, e.g. band-sdk-v4.0.0"
    )
    tag = parser.parse_args().tag
    try:
        pr_url = asyncio.run(changelog(tag))
    except ChangelogRunFailed as failure:
        logger.error("Changelog run for %s failed: %s", tag, failure)
        raise SystemExit(1) from None
    logger.info("Changelog PR for %s: %s", tag, pr_url)


if __name__ == "__main__":
    main()
