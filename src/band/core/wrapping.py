"""Reaching and replacing the concrete tools inside a tools proxy chain."""

from __future__ import annotations

from band.core.protocols import AgentToolsProtocol


class ToolsWrapper:
    """Base for tools proxies that delegate to an inner tools object.

    A turn's tools arrive already proxied — the backend wraps them to observe
    deliveries — so code that needs the concrete ``AgentTools`` underneath, or
    needs to substitute its own, must not test the object it was handed with
    ``isinstance``. Going through :func:`innermost_tools` and
    :func:`substitute_innermost_tools` finds the real tools *and* keeps every
    proxy above it observing the calls it wraps.
    """

    _inner: AgentToolsProtocol

    @property
    def inner(self) -> AgentToolsProtocol:
        """The tools this proxy delegates to (one level down)."""
        return self._inner

    def replace_inner(self, tools: AgentToolsProtocol) -> None:
        """Delegate to ``tools`` from now on."""
        self._inner = tools


def innermost_tools(tools: AgentToolsProtocol) -> AgentToolsProtocol:
    """The concrete tools at the bottom of ``tools``' proxy chain."""
    while isinstance(tools, ToolsWrapper):
        tools = tools.inner
    return tools


def substitute_innermost_tools(
    tools: AgentToolsProtocol, replacement: AgentToolsProtocol
) -> AgentToolsProtocol:
    """Swap in ``replacement`` for the concrete tools, keeping the proxies.

    Returns the object to keep using: the original outermost proxy, so calls
    still flow through it, or ``replacement`` itself when nothing wrapped it.
    """
    deepest: ToolsWrapper | None = None
    cursor: AgentToolsProtocol = tools
    while isinstance(cursor, ToolsWrapper):
        deepest = cursor
        cursor = cursor.inner
    if deepest is None:
        return replacement
    deepest.replace_inner(replacement)
    return tools
