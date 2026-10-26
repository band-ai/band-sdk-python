"""The one place model-written text is checked and made safe for each destination."""

from __future__ import annotations

import re
from collections.abc import Callable

# Any of these in model text means a secret or webhook leaked into an item.
CREDENTIAL_PATTERNS = (
    "sk-ant-",
    "ghp_",
    "ghs_",
    "github_pat_",
    "hooks.slack.com/",
    "discord.com/api/webhooks/",
)
URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)\S+")

# A fenced block, or an inline code span closed by a backtick run of equal length.
CODE_PATTERN = re.compile(
    r"^```[^\n]*\n.*?^```[ \t]*$|(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)",
    re.DOTALL | re.MULTILINE,
)
MDX_SPECIALS = re.compile(r"[{}<>]")
DISCORD_MENTIONS = re.compile(r"@(?:everyone|here)\b")
GITHUB_REFERENCE = re.compile(
    r"https?://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+\S*"
    r"|(?<![\w`])@[\w-]+"
    r"|(?<![\w`&])#\d+\b"
)


def unsafe_findings(text: str) -> list[str]:
    """Name every credential pattern or URL found in ``text``."""
    credentials = [pattern for pattern in CREDENTIAL_PATTERNS if pattern in text]
    return credentials + URL_PATTERN.findall(text)


def slack_text(text: str) -> str:
    """Escape the three characters Slack mrkdwn treats as control syntax."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def discord_text(text: str) -> str:
    """Drop channel-wide pings; the formatter adds its own."""
    return DISCORD_MENTIONS.sub("", text)


def mdx_text(text: str) -> str:
    """Escape MDX expression and JSX characters outside code."""
    return outside_code(text, lambda plain: MDX_SPECIALS.sub(r"\\\g<0>", plain))


def pr_body_text(text: str) -> str:
    """Wrap mentions, issue numbers, and issue links so GitHub leaves them inert."""
    return outside_code(text, lambda plain: GITHUB_REFERENCE.sub(r"`\g<0>`", plain))


def outside_code(text: str, transform: Callable[[str], str]) -> str:
    """Apply ``transform`` to the text between code spans and fenced blocks."""
    pieces: list[str] = []
    position = 0
    for code in CODE_PATTERN.finditer(text):
        pieces += [transform(text[position : code.start()]), code.group(0)]
        position = code.end()
    pieces.append(transform(text[position:]))
    return "".join(pieces)
