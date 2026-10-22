"""Public failures from contract transports and owned local processes."""

from __future__ import annotations


class ContractError(Exception):
    """Report a contract operation that did not establish its requested outcome."""


class ContractProtocolError(ContractError):
    """Report a response that cannot establish the requested contract outcome."""


class ContractIdentityError(ContractProtocolError):
    """Refuse an authority response for a different actor or session."""


class ContractTransportError(ContractError):
    """Report unknown delivery; retry only the same operation identity and body."""


class ContractHTTPError(ContractError):
    """Report a bounded HTTP failure without exposing credentials or response text."""

    def __init__(self, status: int, code: str | None = None) -> None:
        self.status = status
        self.code = code
        super().__init__(f"Contract authority returned HTTP {status}")


class ContractInboxConflict(ContractHTTPError):
    """Require explicit reconciliation when a cursor exceeds restored history."""


class ContractCapacityError(ContractError):
    """Report a locally measured wire limit without claiming remote acceptance."""

    def __init__(self, direction: str) -> None:
        self.direction = direction
        super().__init__(f"Contract {direction} capacity exceeded")


class ContractProposalRejected(ContractError):
    """Report an authority rejection without recording a successful SDK tool action."""


class ContractProcessError(ContractError):
    """Report failed startup, incomplete shutdown, or an unsuccessful operator command."""


class ContractRuntimeUnsupported(ContractError):
    """Require a qualified Linux and Python 3.12 runtime for local execution."""
