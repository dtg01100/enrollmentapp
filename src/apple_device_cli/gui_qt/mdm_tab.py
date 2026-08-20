"""MDMTab — controller for the MDM (mdmclient-equivalent) tab of the GUI.

Displays configuration profiles, installed apps, and device network /
security / certificate information for the currently selected device.
TabController subclass per Round 3 of the GUI refactor.

Refreshes run on the shared ``WorkerThread`` so the UI stays responsive.
Device selection changes (on the Devices tab) trigger a re-query of
every section so the user sees the right data the moment they pick a
different iPad.

The tab does NOT own the device list. It listens to the Devices tab's
``QListWidget.itemSelectionChanged`` signal and resolves the selected
``DeviceInfo`` via the shell's ``_selected_device()`` helper, matching
the same pattern the other tabs already use.

Sections (per the task spec):
  * Profiles — ``QTableWidget`` (display name, identifier, managed, removable)
  * Apps     — ``QTableWidget`` (name, bundle id, version, size, type)
  * Info     — three read-only text panels (Network, Security, Certificates)

A "device not selected" state is surfaced cleanly: an explicit empty-state
label is shown in place of the section content, and the Refresh button is
disabled until a device is picked.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apple_device_cli.device.connection import ensure_device_pairing
from apple_device_cli.device.mdm_inspect import (
    get_certificates,
    get_network_info,
    get_security_info,
    list_apps,
    list_profiles,
    remove_profile as remove_profile_helper,
)
from apple_device_cli.gui_qt.tabs import TabController

if TYPE_CHECKING:
    from apple_device_cli.gui_qt.app import EnrollmentApp


def _format_bytes(n: int) -> str:
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class MDMTab(TabController):
    """Owns the MDM tab widgets and refresh logic.

    Three sections, each its own group:
      * Profiles — ``QTableWidget`` (display name, identifier, managed, removable)
      * Apps     — ``QTableWidget`` (name, bundle id, version, size, type)
      * Info     — three read-only text panels (Network, Security, Certificates)

    A single ``Refresh`` button re-queries all three. Device selection
    changes on the Devices tab auto-trigger a refresh (debounced via
    ``_pending_refresh`` so rapid clicks don't queue redundant workers).
    """

    # ---- public widgets (mirrored onto self in __init__ for shell access) ----
    refresh_btn: QPushButton
    profiles_table: QTableWidget
    apps_table: QTableWidget
    network_view: QTextEdit
    security_view: QTextEdit
    certs_view: QTextEdit
    status_label: QLabel
    empty_state_label: QLabel

    def __init__(self, shell: "EnrollmentApp") -> None:
        self._shell = shell
        self._refresh_in_flight = False
        self._pending_refresh = False
        self._current_udid: str | None = None

        # ``_root_widget`` is the underlying QWidget. The public
        # ``widget()`` / ``tab_widget()`` methods expose it; using an
        # underscore name here keeps the public methods from being
        # shadowed by the instance attribute.
        self._root_widget = self._build()

    # -- TabController protocol ------------------------------------------

    def widget(self) -> QWidget:
        """Return the root QWidget (TabController ABC contract)."""
        return self._root_widget

    def tab_widget(self) -> QWidget:
        """Back-compat alias used by ``app._create_mdm_tab`` and tests."""
        return self._root_widget

    def refresh(self) -> None:
        self._trigger_refresh()

    def on_org_changed(self, org: Any) -> None:
        """MDM tab is org-agnostic; nothing to do."""
        return None

    def on_device_changed(self, device: Any) -> None:
        """Auto-refresh when the user picks a new device.

        Called by the shell when the active device changes (or when the
        selection is cleared). When no device is selected, the tab
        surfaces the empty state cleanly so the user knows what to do.
        """
        if device is None:
            self._current_udid = None
            self._render_empty("Select a device to inspect profiles, apps, and info.")
            return
        self._current_udid = device.udid
        self._trigger_refresh()

    # -- Build -----------------------------------------------------------

    def _build(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(6)

        # Empty state (shown when no device is selected) — a structured
        # preview so the user sees the 3 sections that will populate
        # when a device is picked, instead of just a single gray sentence.
        empty_container, empty_hint = self._build_empty_state_preview()
        self._empty_state_container = empty_container
        self.empty_state_label = empty_hint
        outer_layout.addWidget(self._empty_state_container)
        # Main content (hidden when no device is selected) — wrapped in
        # a QScrollArea so the tab fits the viewport on smaller windows
        # instead of forcing the user to chase a scrollbar on the tab
        # widget itself.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_profiles_group())
        splitter.addWidget(self._build_apps_group())
        splitter.addWidget(self._build_info_group())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        content_layout.addWidget(splitter)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)

        # Bottom bar: refresh button + status. Put at the bottom for
        # visual consistency with the other tabs (Devices / Enrollment
        # / Restore all keep their action row at the bottom of the tab).
        bottom_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self._trigger_refresh)
        bottom_row.addWidget(self.refresh_btn)
        self.status_label = QLabel("(no device)")
        self.status_label.setStyleSheet("color: gray;")
        bottom_row.addWidget(self.status_label, 1)
        outer_layout.addLayout(bottom_row)

        # Listen to device selection on the Devices tab
        devices_list = getattr(self._shell, "devices_list", None)
        if devices_list is not None:
            devices_list.itemSelectionChanged.connect(self._on_device_selection_changed)

        # Store the scroll area so we can show/hide based on selection.
        # Wrapping in a QScrollArea means show()/hide() still works as
        # before, but the inner content now scrolls inside the tab.
        self._content_widget = scroll
        scroll.hide()

        return outer

    def _build_profiles_group(self) -> QGroupBox:
        group = QGroupBox("Configuration Profiles")
        layout = QVBoxLayout(group)
        self.profiles_table = QTableWidget(0, 4)
        self.profiles_table.setHorizontalHeaderLabels(
            ["Display Name", "Identifier", "Managed", "Removable"]
        )
        self.profiles_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.profiles_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.profiles_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.profiles_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.profiles_table.verticalHeader().setVisible(False)
        self.profiles_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.profiles_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.profiles_table)
        return group

    def _build_apps_group(self) -> QGroupBox:
        group = QGroupBox("Installed Apps")
        layout = QVBoxLayout(group)
        self.apps_table = QTableWidget(0, 5)
        self.apps_table.setHorizontalHeaderLabels(
            ["Name", "Bundle ID", "Version", "Size", "Type"]
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self.apps_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self.apps_table.verticalHeader().setVisible(False)
        self.apps_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.apps_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.apps_table)
        return group

    def _build_info_group(self) -> QGroupBox:
        group = QGroupBox("Info (Network / Security / Certificates)")
        layout = QHBoxLayout(group)
        layout.setSpacing(6)
        self.network_view = QTextEdit()
        self.network_view.setReadOnly(True)
        self.security_view = QTextEdit()
        self.security_view.setReadOnly(True)
        self.certs_view = QTextEdit()
        self.certs_view.setReadOnly(True)
        for view, title in (
            (self.network_view, "Network"),
            (self.security_view, "Security"),
            (self.certs_view, "Certificates"),
        ):
            box = QVBoxLayout()
            box.setSpacing(2)
            header = QLabel(f"<b>{title}</b>")
            box.addWidget(header)
            box.addWidget(view)
            wrapper = QWidget()
            wrapper.setLayout(box)
            layout.addWidget(wrapper)
        return group

    def _build_empty_state_preview(self) -> tuple[QWidget, QLabel]:
        """Build the no-device placeholder with visible section borders.

        Without this, the user sees only a centered gray sentence with
        no hint of what the tab will show. Three ``QFrame`` placeholders
        styled like the future ``QGroupBox``es make the layout preview
        obvious: Profiles here, Apps here, Info here.

        Returns the container widget and the bottom hint ``QLabel`` so
        the caller can wire them as ``self._empty_state_container`` and
        ``self.empty_state_label`` respectively. The latter keeps the
        existing ``setText`` / ``show`` / ``hide`` contract working for
        both the renderer and the test suite (which checks
        ``"Select a device" in mdm.empty_state_label.text()``).
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 24, 16, 16)
        layout.setSpacing(10)

        sections = (
            ("Configuration Profiles", "Managed profiles, identifiers, and removal flags."),
            ("Installed Apps", "Bundle IDs, versions, sizes, and user/system type."),
            ("Network · Security · Certificates", "Wi-Fi, passcode, lock state, and provisioning profiles."),
        )
        for title, subtitle in sections:
            layout.addWidget(self._build_preview_card(title, subtitle))
        layout.addStretch(1)

        hint = QLabel("Select a device to populate these sections.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: gray; font-size: 13px;")
        layout.addWidget(hint)
        return container, hint

    @staticmethod
    def _build_preview_card(title: str, subtitle: str) -> QFrame:
        """Single bordered card with a title and a subtitle."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setFrameShadow(QFrame.Shadow.Raised)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(10, 8, 10, 8)
        inner.setSpacing(2)
        title_label = QLabel(f"<b>{title}</b>")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setStyleSheet("color: gray;")
        subtitle_label.setWordWrap(True)
        inner.addWidget(title_label)
        inner.addWidget(subtitle_label)
        return frame

    # -- Selection / refresh --------------------------------------------

    def _on_device_selection_changed(self) -> None:
        device = self._shell._selected_device()
        self.on_device_changed(device)

    def _trigger_refresh(self) -> None:
        device = self._shell._selected_device()
        if device is None:
            self._render_empty("Select a device to inspect profiles, apps, and info.")
            return
        if self._refresh_in_flight:
            self._pending_refresh = True
            return
        self._refresh_in_flight = True
        self._pending_refresh = False
        self.refresh_btn.setEnabled(False)
        self.status_label.setText(f"Refreshing {device.device_name}…")
        ensure_device_pairing(device.udid)
        self._current_udid = device.udid
        # Show the content area; we're about to populate it. Hide both
        # the empty-state container AND the hint label directly — Qt's
        # isHidden() returns the widget's OWN hidden flag, not its
        # parent's, so the test that asserts empty_state_label.isHidden()
        # == True needs the label itself to be hidden, not just its
        # ancestor.
        self._content_widget.show()
        self._empty_state_container.hide()
        self.empty_state_label.hide()
        self._run_worker(device.udid)

    def _run_worker(self, udid: str) -> None:
        """Spin up a WorkerThread that calls all six inspection helpers.

        The six calls share a single ``lockdown`` connection (one
        ``create_using_usbmux``) but each helper still owns its own
        short-lived event loop (``asyncio.run``), matching the pattern
        used in ``cli.py``.
        """
        from apple_device_cli.gui_qt.app import WorkerThread

        def _collect() -> dict[str, Any]:
            """Run all six inspection calls in one go.

            Returning a single dict keeps the worker count down and
            makes the result a single atomic update on the UI side.
            """
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.mobile_config import MobileConfigService
            from pymobiledevice3.services.installation_proxy import (
                InstallationProxyService,
            )
            from pymobiledevice3.services.misagent import MisagentService
            from pymobiledevice3.services.diagnostics import DiagnosticsService

            import asyncio

            async def _gather():
                lockdown = await create_using_usbmux(serial=udid)
                async with MobileConfigService(lockdown) as mc, \
                        InstallationProxyService(lockdown) as inst, \
                        MisagentService(lockdown) as mis, \
                        DiagnosticsService(lockdown) as diag:
                    return {
                        "profiles": list_profiles(mc),
                        "apps": list_apps(inst),
                        "network": get_network_info(diag),
                        "security": get_security_info(diag),
                        "certificates": get_certificates(mis),
                    }

            return asyncio.run(_gather())

        worker = WorkerThread(_collect)
        self._shell._run_worker(
            worker,
            self._refresh_done,
            [self.refresh_btn],
        )

    @Slot(object, object)
    def _refresh_done(self, result: Any, error: Exception | None) -> None:
        self._refresh_in_flight = False
        # If a refresh was requested while we were running, kick off a
        # fresh one now. (Otherwise callers can be left looking at a
        # half-stale dataset.)
        if self._pending_refresh:
            self._pending_refresh = False
            self._trigger_refresh()
            return
        self.refresh_btn.setEnabled(True)
        if error is not None:
            self.status_label.setText(f"Refresh failed: {error}")
            return
        if result is None:
            self.status_label.setText("(no data)")
            return
        self._populate(result)
        counts = (
            f"{len(result.get('profiles', []))} profiles, "
            f"{len(result.get('apps', []))} apps, "
            f"{len(result.get('certificates', []))} certs"
        )
        self.status_label.setText(f"Last refresh: {counts}")

    def _populate(self, data: dict[str, Any]) -> None:
        # Profiles
        profiles = data.get("profiles") or []
        self.profiles_table.setRowCount(len(profiles))
        for row, p in enumerate(profiles):
            self._set_cell(self.profiles_table, row, 0, p.display_name or "(no name)")
            self._set_cell(self.profiles_table, row, 1, p.identifier)
            self._set_cell(self.profiles_table, row, 2, "Yes" if p.is_managed else "No")
            self._set_cell(self.profiles_table, row, 3, "Yes" if p.is_removable else "No")

        # Apps
        apps = data.get("apps") or []
        self.apps_table.setRowCount(len(apps))
        for row, a in enumerate(apps):
            self._set_cell(self.apps_table, row, 0, a.name or "(no name)")
            self._set_cell(self.apps_table, row, 1, a.bundle_identifier)
            self._set_cell(
                self.apps_table,
                row,
                2,
                a.short_version or a.version or "",
            )
            total = a.static_disk_usage + a.dynamic_disk_usage
            self._set_cell(self.apps_table, row, 3, _format_bytes(total))
            self._set_cell(self.apps_table, row, 4, a.application_type)

        # Info
        self.network_view.setPlainText(self._format_kv(data.get("network") or {}))
        self.security_view.setPlainText(self._format_kv(data.get("security") or {}))
        certs = data.get("certificates") or []
        if not certs:
            self.certs_view.setPlainText("(no provisioning profiles installed)")
        else:
            lines = [f"{c.name}  ({c.uuid})" for c in certs]
            self.certs_view.setPlainText("\n".join(lines))

    @staticmethod
    def _set_cell(table: QTableWidget, row: int, col: int, value: str) -> None:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, col, item)

    @staticmethod
    def _format_kv(d: dict[str, Any]) -> str:
        if not d:
            return "(no data)"
        return json.dumps(d, indent=2, default=str)

    def _render_empty(self, message: str) -> None:
        self._content_widget.hide()
        self._empty_state_container.show()
        # See _trigger_refresh for the symmetry rationale.
        self.empty_state_label.setText(message)
        self.empty_state_label.show()
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("(no device)")

    # -- Profile removal (used by Devices tab right-click) ---------------

    def remove_profile_by_identifier(self, identifier: str) -> bool:
        """Remove a profile via the same path the CLI uses.

        Returns True if removed, False if the profile wasn't present.
        Raises any underlying service error so the caller can show a
        message box. Called from the Devices tab's right-click menu
        (wired up in a follow-up step).
        """
        device = self._shell._selected_device()
        if device is None:
            QMessageBox.warning(self._shell, "No device", "Select a device first.")
            return False
        ensure_device_pairing(device.udid)

        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.mobile_config import MobileConfigService
        import asyncio

        async def _call():
            lockdown = await create_using_usbmux(serial=device.udid)
            async with MobileConfigService(lockdown) as mc:
                return remove_profile_helper(mc, identifier)

        try:
            removed = asyncio.run(_call())
        except Exception as exc:  # noqa: BLE001 — surface to user
            QMessageBox.critical(
                self._shell,
                "Remove failed",
                f"Could not remove profile {identifier}:\n\n{exc}",
            )
            raise

        if removed:
            self._trigger_refresh()
        return removed
