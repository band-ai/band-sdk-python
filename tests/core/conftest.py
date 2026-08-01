"""Core-package fixtures.

Contract helpers live in ``tests.core.contractsupport`` and are imported
explicitly by contract tests — do not load them via ``pytest_plugins`` here
(pytest forbids non-top-level plugin registration).
"""
