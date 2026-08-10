"""DevicesTab — controller for the Devices tab of the GUI.

Extracted from the monolithic ``EnrollmentApp`` in Round 3 of the GUI
refactor. Owns the device list, the toolbar buttons (Refresh / Show
Info / Activate / Pair / Trust), and the right-click context menu.
Defers blocking USB IO to the shared ``WorkerPool``; cross-tab
side-effects (populating the Enrollment UDID combo, the Restore device
combo, mode labels, status bar) are dispatched through the ``shell``
reference passed in at construction.

Back-compat shims on ``EnrollmentApp`` (set in ``app.py``) forward
attribute reads for the devices widgets (``devices_list``,
``refresh_devices_btn``, ``device_info_btn``, ``activate_btn``,
``pair_btn``, ``devices_empty_label``) to this controller so the
existing tests keep passing without rewrites.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apple_device_cli.device.connection import (
    ensure_device_pairing,
    get_device_info,
)
from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.enrollment.activation import activate_device


def _list_devices():
    """Resolve list_devices through the package module each call.

    Tests patch ``apple_device_cli.gui_qt.list_devices``; this deferred
    lookup ensures the patched version is used by the worker thread.
    """
    from apple_device_cli import gui_qt

    return gui_qt.list_devices()

if TYPE_CHECKING:
    from apple_device_cli.gui_qt.app import EnrollmentApp


class DevicesTab:
    """Owns the Devices tab widgets and refresh/action logic.

    Held by ``EnrollmentApp`` (which is the Qt ``QMainWindow`` subclass);
    exposes its widgets as attributes so the existing test suite that
    reaches into ``app.devices_list`` etc. keeps working. Implements
    the ``TabController`` protocol via duck-typing (``widget``,
    ``refresh``, ``on_org_changed``, ``on_device_changed``) — formal
    ABC inheritance comes when the other tabs land.
    """

    def __init__(self, shell: "EnrollmentApp") -> None:
        self._shell = shell
        self.widget = self._build()
        # Public-facing widgets mirrored as attributes so the shell can
        # expose them via __getattr__ and tests can reach in directly.
        self.devices_list: QListWidget
        self.devices_empty_label: QLabel
        self.refresh_devices_btn: QPushButton
        self.device_info_btn: QPushButton
        self.activate_btn: QPushButton
        self.pair_btn: QPushButton

    # -- TabController protocol ------------------------------------------

    def tab_widget(self) -> QWidget:
        return self.widget

    def refresh(self) -> None:
        self._refresh()

    def on_org_changed(self, org: Any) -> None:
        """Devices tab is org-agnostic; nothing to do."""
        return None

    def on_device_changed(self, device: Any) -> None:
        """Selection happens inside the list; nothing to do externally."""
        return None

    # -- Build -----------------------------------------------------------

    def _build(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.refresh_devices_btn = QPushButton("Refresh Devices")
        self.refresh_devices_btn.clicked.connect(self._refresh)
        toolbar.addWidget(self.refresh_devices_btn)

        self.device_info_btn = QPushButton("Show Device Info")
        self.device_info_btn.clicked.connect(self._show_device_info)
        toolbar.addWidget(self.device_info_btn)

        self.activate_btn = QPushButton("Activate")
        self.activate_btn.clicked.connect(self._activate_device)
        toolbar.addWidget(self.activate_btn)

        self.pair_btn = QPushButton("Pair / Trust")
        self.pair_btn.clicked.connect(self._pair_device)
        toolbar.addWidget(self.pair_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.devices_list = QListWidget()
        self.devices_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.devices_list.customContextMenuRequested.connect(
            self._show_context_menu
        )
        layout.addWidget(self.devices_list, 1)

        self.devices_empty_label = QLabel(
            "No devices found. Connect an iOS device over USB and "
            "click Refresh Devices."
        )
        self.devices_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.devices_empty_label.setStyleSheet(
            "color: palette(mid); font-size: 13px; padding: 4px;"
        )
        layout.addWidget(self.devices_empty_label)
        self.devices_empty_label.setVisible(self.devices_list.count() == 0)

        return widget

    # -- Refresh + completion -------------------------------------------

    def _refresh(self) -> None:
        self._shell._log("Refreshing device list...")
        token = self._shell._next_token()
        buttons = [self.refresh_devices_btn]
        restore_btn = getattr(self._shell, "restore_refresh_devices_btn", None)
        if restore_btn is not None:
            buttons.append(restore_btn)
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(_list_devices)
        self._shell._run_worker(worker, self._on_refreshed, buttons, token=token)

    @Slot(object, object)
    def _on_refreshed(self, result: Any, error: Exception | None, token: int) -> None:
        if not self._shell._is_current_token(token):
            return
        if error:
            self._shell._log(f"Failed to list devices: {error}")
            return
        devices = result or []
        self._shell._devices = list(devices)
        self.devices_list.clear()
        for device in self._shell._devices:
            display = f"{device.device_name}  ({device.udid})"
            QListWidgetItem(display, self.devices_list)
        self.devices_empty_label.setVisible(self.devices_list.count() == 0)
        if self._shell._devices:
            self.devices_list.setCurrentRow(0)
        self._shell._update_enroll_udids()
        self._shell._populate_restore_device_combo()
        self._shell._update_mode_labels()
        self._shell._log(f"Found {len(self._shell._devices)} device(s).")
        self._shell._update_status_bar()

    # -- Per-device actions ---------------------------------------------

    def _selected_device(self) -> DeviceInfo | None:
        row = self.devices_list.currentRow()
        if row < 0 or row >= len(self._shell._devices):
            return None
        return self._shell._devices[row]

    def _show_device_info(self) -> None:
        device = self._selected_device()
        if not device:
            self._shell._warn_no_device()
            return
        self._shell._log(f"Fetching info for {device.device_name}...")
        udid = device.udid
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(lambda: get_device_info(udid))
        self._shell._run_worker(worker, self._on_device_info, [self.device_info_btn])

    @Slot(object, object)
    def _on_device_info(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Failed to get device info: {error}")
            return
        info = result
        if not info:
            self._shell._log("Device info unavailable.")
            return
        self._shell._log(f"UDID: {info.udid}")
        self._shell._log(f"Name: {info.device_name}")
        self._shell._log(f"Type: {info.device_type}")
        self._shell._log(f"iOS: {info.firmware_version} ({info.build_version})")
        if info.ecid:
            self._shell._log(f"ECID: {info.ecid}")

    def _activate_device(self) -> None:
        device = self._selected_device()
        if not device:
            self._shell._warn_no_device()
            return
        self._shell._log(f"Activating {device.device_name}...")
        udid = device.udid
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(lambda: activate_device(udid))
        self._shell._run_worker(worker, self._on_activation_result, [self.activate_btn])

    @Slot(object, object)
    def _on_activation_result(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Activation failed: {error}")
        else:
            self._shell._log("Activation completed.")

    def _pair_device(self) -> None:
        device = self._selected_device()
        if not device:
            self._shell._warn_no_device()
            return
        self._shell._log(f"Ensuring pairing with {device.device_name}...")
        udid = device.udid
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(lambda: ensure_device_pairing(udid))
        self._shell._run_worker(worker, self._on_pair_result, [self.pair_btn])

    @Slot(object, object)
    def _on_pair_result(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Pairing failed: {error}")
        else:
            self._shell._log("Device paired/trusted successfully.")

    # -- Context menu ----------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        item = self.devices_list.itemAt(pos)
        if item is None:
            return
        self.devices_list.setCurrentRow(self.devices_list.row(item))
        menu = self._build_context_menu()
        menu.exec(self.devices_list.mapToGlobal(pos))

    def _build_context_menu(self) -> QMenu:
        menu = QMenu(self.devices_list)
        menu.addAction("Show Device Info", self._show_device_info)
        menu.addAction("Activate", self._activate_device)
        menu.addAction("Pair / Trust", self._pair_device)
        # Make Supervised is hidden when there's no org selected — clicking
        # it with no org would just bounce to the Enrollment tab with no
        # way forward. Gating tells us whether the action can succeed.
        if self._shell._gating.can_enroll():
            menu.addAction("Make Supervised", self._make_supervised_from_context)
        return menu

    def _make_supervised_from_context(self) -> None:
        self._shell.tabs.setCurrentWidget(self._shell.enroll_tab)
        self._shell._update_enroll_action_gates()
        if self._shell.guided_enroll_btn.isEnabled():
            self._shell._guided_enroll()
        else:
            self._shell._log(
                "Switched to Enrollment tab — pick an organization "
                "to start the workflow."
            )