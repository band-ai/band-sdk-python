from __future__ import annotations

from band.runtime.tools import canonicalize_mcp_tool_name


class TestCanonicalizeMcpToolName:
    """Recovering the canonical band name from an MCP ``<server>-`` spelling."""

    OWN = frozenset({"band_send_message", "echo"})

    def test_strips_server_prefix_off_own_tool(self) -> None:
        name = canonicalize_mcp_tool_name("band-band_send_message", self.OWN)
        assert name == "band_send_message"

    def test_custom_tool_prefix_stripped(self) -> None:
        assert canonicalize_mcp_tool_name("band-echo", self.OWN) == "echo"

    def test_non_band_server_prefix_passes_through(self) -> None:
        """Only the Band MCP server's own ``band-`` prefix resolves -- an
        unrelated server's tool that happens to end in one of our names must
        not be misattributed to us (e.g. narrated/suppressed as our own)."""
        name = canonicalize_mcp_tool_name("platform-band_send_message", self.OWN)
        assert name == "platform-band_send_message"

    def test_foreign_tool_passes_through(self) -> None:
        """A prefixed name that reveals none of ours stays as reported."""
        assert canonicalize_mcp_tool_name("band-grep", self.OWN) == "band-grep"

    def test_unprefixed_name_passes_through(self) -> None:
        assert (
            canonicalize_mcp_tool_name("band_send_message", self.OWN)
            == "band_send_message"
        )
