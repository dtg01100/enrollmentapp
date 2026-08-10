"""Gating helper for the GUI shell.

`_Gating` lives on `MainWindow` (so it sees org + device presence
state) and exposes a single ``evaluate()`` method that returns the
current gating decision for buttons that depend on having both an
organization and a device selected. Tab controllers consult it instead
of re-deriving the same org/device state from their own widgets.

Closes Round 2's open question: the Devices tab context menu's "Make
Supervised" entry now hides when no org is selected (instead of being
a no-op that just switches tabs).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatingState:
    """Snapshot of the gating inputs."""

    has_org: bool
    has_device: bool

    @property
    def can_enroll(self) -> bool:
        """True when an enrollment action has both prerequisites."""
        return self.has_org and self.has_device


class _Gating:
    """Tracks org + device presence for button-enable decisions.

    The shell instantiates one of these and forwards ``org_changed`` /
    ``device_changed`` events. Tabs call ``state()`` to read the
    current snapshot; ``evaluate_for(action)`` returns whether a
    specific action should be enabled.

    Pure data, no Qt. The shell updates state on selection changes;
    tabs never write.
    """

    def __init__(self) -> None:
        self._has_org: bool = False
        self._has_device: bool = False

    # -- Shell-side update API ------------------------------------------

    def set_org(self, org: Any | None) -> None:
        self._has_org = org is not None

    def set_device(self, device: Any | None) -> None:
        self._has_device = device is not None

    def clear(self) -> None:
        self._has_org = False
        self._has_device = False

    # -- Tab-side read API ----------------------------------------------

    def state(self) -> GatingState:
        return GatingState(has_org=self._has_org, has_device=self._has_device)

    def can_enroll(self) -> bool:
        return self._has_org and self._has_device

    def evaluate_for(self, action: str) -> bool:
        """Action-aware gating — extensible for future actions.

        Currently only ``"enroll"`` is recognized (org + device).
        Returns False for unknown actions so callers fail safe.
        """
        if action == "enroll":
            return self.can_enroll()
        return False