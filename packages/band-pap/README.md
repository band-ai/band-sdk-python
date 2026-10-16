# Band PAP integration

Development package for a generic Personal Agent Protocol bridge into Band's
agent collaboration and communication layer. The target includes outbound
company participants and an inbound gateway, with independent connections in
one Band room. It has no agent-framework dependency or business-policy engine.

This is not a usable PAP adapter or server yet. The initial implementation
validates discovery documents and their domain/issuer binding without making
network calls. Authentication, persistence, forwarding, and gateway endpoints
remain unimplemented. Do not use discovery validation as authorization.

The maintained contract and completion ledger are in the repository's
`docs/pap/contract.md` and `docs/pap/implementation-ledger.md`. Both independent
Sol reviews must be resolved for every ledger item before it is complete.
