"""Slack bridge adapter - re-exports from integrations module."""

from band.integrations.slack.adapter import SlackAdapter, SlackAdapterConfig
from band.integrations.slack.types import SlackApp, SlackSessionState

__all__ = ["SlackAdapter", "SlackAdapterConfig", "SlackApp", "SlackSessionState"]
