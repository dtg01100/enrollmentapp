"""Tab controller base class for the GUI refactor.

Each top-level tab in ``MainWindow`` (Devices / Orgs / Enroll / Restore) is
encapsulated by a ``TabController`` subclass that owns its widgets, its
refresh logic, and reacts to global selection changes. The shell
``MainWindow`` instantiates each controller, places its ``widget()`` into
the appropriate ``QTabWidget`` page, and forwards user actions
(org/device selection, refresh button, auto-refresh timer) via the
controller methods.

This is the structural backbone for Round 3's split. Implementations
(``DevicesTab`` / ``OrgsTab`` / ``EnrollTab`` / ``RestoreTab``) are
defined in a later step; this module establishes the contract and
provides a ``_NullTab`` test double so tests can assert interface
conformance without dragging in PySide6.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class TabController(ABC):
    """Contract every tab controller implements.

    The shell ``MainWindow`` interacts with tabs only through this
    interface. Implementations are expected to:

    * Build their widgets eagerly in ``__init__`` (no Qt work in the
      controller's own initializer is fine; ``widget()`` returns the
      root after the constructor returns).
    * Treat ``refresh()`` as idempotent — calling it twice in a row
      without intervening state changes should be a cheap no-op.
    * Make ``on_org_changed`` / ``on_device_changed`` re-evaluate any
      button-enable logic that depends on the current selection, but
      NOT trigger a refresh on their own — the shell decides when to
      refresh (manual button, auto-refresh timer).
    """

    @abstractmethod
    def widget(self) -> QWidget:
        """Return the root QWidget for placement into a QTabWidget page."""

    @abstractmethod
    def refresh(self) -> None:
        """Reload the tab's data from its backing store.

        Implementations must defer blocking IO to a worker thread and
        gate any UI input controls (buttons, combos) while the refresh
        is in flight. Calling refresh() repeatedly while a refresh is
        already in flight is permitted; the implementation may dedupe
        via token counters.
        """

    @abstractmethod
    def on_org_changed(self, org: Any) -> None:
        """Called when the active organization changes.

        ``org`` is an ``Organization`` instance from
        ``apple_device_cli.orgs.manager``, or ``None`` when the selection
        is cleared. Implementations update any org-dependent UI
        (combo boxes, action buttons, banners).
        """

    @abstractmethod
    def on_device_changed(self, device: Any) -> None:
        """Called when the active device changes.

        ``device`` is a ``DeviceInfo`` from
        ``apple_device_cli.device.info``, or ``None`` when cleared.
        Implementations update device-dependent UI.
        """


class _NullTab(TabController):
    """No-op TabController for unit tests.

    Lets tests assert interface conformance and exercise shell wiring
    without instantiating real Qt widgets. Every method returns
    ``None``; ``widget()`` raises so tests catch callers that forget to
    swap in a real tab when the widget is actually needed.
    """

    def widget(self):  # type: ignore[override]
        raise NotImplementedError(
            "_NullTab.widget() is a stub; tests must substitute a real controller."
        )

    def refresh(self) -> None:
        return None

    def on_org_changed(self, org: Any) -> None:
        return None

    def on_device_changed(self, device: Any) -> None:
        return None