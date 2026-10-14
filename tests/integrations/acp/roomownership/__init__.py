"""An ACP room owns its process, session and work until a release confirms it.

Each test drives a real adapter over real ACP JSON-RPC: an in-process fake
agent, or the stdio peer subprocess where the process boundary itself is under
test. Barriers hold the exact step a race needs; only a transition's own
time bound is ever waited out.
"""
