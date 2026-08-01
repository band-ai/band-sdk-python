"""Tool-first reply / delivery-receipt conformance.

Layering (dependency flows downward):

    scenarios  — contract rows (adapter × action → receipt / text fallback)
    tools      — fake-tools construction and stable fixture inputs
    outcome    — TurnOutcome data and contract assertions
    checks     — delivery observations and in-process scenario driving
    runners    — thin per-adapter drivers that produce a TurnOutcome

Import from the submodule you need — this package does not re-export a barrel.

Tests live beside the fixtures they exercise:

    test_scenarios.py — scenario semantics on ObservingTools alone
    test_matrix.py    — adapter × scenario matrix via runners
"""
