"""Portable contract clients and explicitly selected local runtime services."""

from __future__ import annotations

from band.exports import lazy_exports

__all__, __getattr__ = lazy_exports(
    __name__,
    client=["ContractClient", "ContractRoom"],
    models=[
        "ContractConnection",
        "ContractIdentity",
        "ContractProposal",
        "InboxPage",
        "JournalQuery",
        "RuntimeConfig",
        "ProcessOptions",
        "ProcessResult",
    ],
    author=["ContractAuthorClient", "AuthorCompletion"],
    programs=[
        "ContractProgramHostClient",
        "ProgramScope",
        "ProgramAssessment",
        "ProgramStart",
        "ProgramTask",
        "ProgramControl",
        "ProgramReplay",
    ],
    service=["ContractService", "ContractAuthorService", "ContractProgramService"],
    journal=["ContractJournal"],
    tools=["contract_tools"],
    errors=[
        "ContractError",
        "ContractProtocolError",
        "ContractIdentityError",
        "ContractTransportError",
        "ContractHTTPError",
        "ContractInboxConflict",
        "ContractCapacityError",
        "ContractProposalRejected",
        "ContractProcessError",
        "ContractRuntimeUnsupported",
    ],
)
