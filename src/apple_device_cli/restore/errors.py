"""Custom exception hierarchy for the restore engine."""
from __future__ import annotations


class RestoreEngineError(RuntimeError):
    """Raised by the restore engine for any non-recoverable failure.

    Every instance carries a user-actionable message — install hints,
    network hints, free-disk hints — so the CLI and GUI can surface
    it directly without translation.
    """
