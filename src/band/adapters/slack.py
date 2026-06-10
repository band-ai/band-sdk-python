"""Slack bridge adapter - re-exports from integrations module."""

from band.integrations.slack.adapter import SlackAdapter
from band.integrations.slack.types import SlackApp, SlackSessionState

__all__ = ["SlackAdapter", "SlackApp", "SlackSessionState"]
