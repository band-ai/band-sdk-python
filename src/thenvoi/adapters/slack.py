"""Slack bridge adapter - re-exports from integrations module."""

from thenvoi.integrations.slack.adapter import SlackAdapter
from thenvoi.integrations.slack.types import SlackApp, SlackSessionState

__all__ = ["SlackAdapter", "SlackApp", "SlackSessionState"]
