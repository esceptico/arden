"""Canonical fact-ledger package.

Import concrete facts modules directly; this boundary must not eagerly load
the ledger, projections, completion adapters, or maintenance runner.
"""

LEDGER_DIRECTORY = "facts"

__all__ = ["LEDGER_DIRECTORY"]
