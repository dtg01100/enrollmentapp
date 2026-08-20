"""Headless render of every top-level GUI tab.

Loads ``EnrollmentApp()`` with seeded sample data (no real device, no real
network, no real certificate), then walks through the five tabs and saves
each as a PNG. Useful for layout audits and visual regression checks
without booting the GUI.

Run:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/render_all_tabs.py [out_dir]

Defaults ``out_dir`` to ``/tmp``. Output: ``/tmp/<tab>-render.png`` for
each tab and ``/tmp/<tab>-empty.png`` for the no-data state where it
exists (Devices, Organizations, MDM).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QListWidgetItem  # noqa: E402  (after env setup)


@dataclass
class FakeDevice:
    udid: str = "00008101-001234567890ABCD"
    device_name: str = "Test iPhone 14 Pro"
    device_type: str = "iPhone15,2"
    firmware_version: str = "17.5"
    build_version: str = "21F79"
    ecid: str = "0xABCDEF1234"
    is_recovery: bool = False


@dataclass
class FakeOrg:
    name: str = "Acme Corporation"
    org_id: str = "ACME-001"
    address: str = "123 Main St, Anytown, USA"
    phone: str = "+1-555-0100"
    email: str = "admin@acme.example"
    mdm_url: str = "https://mdm.acme.example/enroll"
    checkin_url: str = "https://mdm.acme.example/checkin"
    mdm_topic: str = "com.apple.mgmt.acme"
    identity_ref: str = "acme-mdm-identity"
    mdm_description: str = "Acme MDM enrollment"
    cert_path: str = "/home/user/.config/apple_device_cli/orgs/acme/cert.der"
    key_path: str = "/home/user/.config/apple_device_cli/orgs/acme/key.der"
    wifi_config_path: str = "/home/user/.config/apple_device_cli/orgs/acme/wifi.mobileconfig"
    mdm_mobileconfig_path: str = "/home/user/.config/apple_device_cli/orgs/acme/mdm.mobileconfig"
    created_at: str = "2026-01-15T10:30:00Z"


# --- Mocks so EnrollmentApp can boot without a device or org storage ---


def _patch_environment() -> None:
    """Stub out everything EnrollmentApp touches on startup."""
    import apple_device_cli.gui_qt as gq

    # SyncWorker: run blocking handlers inline (the real WorkerThread
    # would never fire its QThread signals in this script because there
    # is no event loop running).
    class _Signal:
        def __init__(self) -> None:
            self._slots: list = []

        def connect(self, slot):
            self._slots.append(slot)

        def emit(self, *args):
            for slot in list(self._slots):
                slot(*args)

    class SyncWorker:
        def __init__(self, fn):
            self.fn = fn
            self.result = None
            self.error: Exception | None = None
            self.completed = _Signal()
            self.finished = _Signal()

        def start(self):
            try:
                self.result = self.fn()
            except Exception as exc:  # noqa: BLE001
                self.error = exc
            self.completed.emit(self.result, self.error)
            self.finished.emit()

        def quit(self):
            pass

        def wait(self, timeout=0):
            return True

    gq.WorkerThread = SyncWorker  # type: ignore[assignment]

    # Empty default device/org lists so the auto-refresh doesn't try to
    # hit pymobiledevice3. We'll inject our sample data directly below.
    fake_devices: list = []
    fake_orgs: list = []

    def fake_list_devices():
        return fake_devices

    def fake_list_orgs():
        return fake_orgs

    gq.list_devices = fake_list_devices  # type: ignore[assignment]
    gq.OrganizationManager.list_orgs = lambda self: fake_orgs  # type: ignore[assignment]


# --- Tab-by-tab data injection ---


def _seed_devices(app) -> None:
    """Populate the Devices tab list with two sample devices."""
    devices = [
        FakeDevice(device_name="Test iPhone 14 Pro", udid="00008101-001234567890ABCD"),
        FakeDevice(
            device_name="Warehouse iPad",
            udid="00008110-0054321CBA9876",
            device_type="iPad13,4",
            firmware_version="17.5",
        ),
    ]
    # Direct mutation so we don't need to wire up the device-list
    # refresh worker — we're just populating the QListWidget.
    for d in devices:
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(f"{d.device_name}  ({d.udid[:8]}…)  iOS {d.firmware_version}")
        item.setData(0x100, d)  # Qt.UserRole
        app.devices_list.addItem(item)
    app.devices_list.setCurrentRow(0)


def _seed_orgs(app) -> None:
    """Populate the Orgs tab list with one sample org."""
    org = FakeOrg()
    # Set the shell's org list BEFORE the QListWidget so the
    # _update_org_details slot (bound to currentRowChanged) can find the
    # right record when we set currentRow(0).
    app._orgs = [org]
    app.orgs_list.clear()
    item = QListWidgetItem(f"{org.name}  ({org.org_id})")
    item.setData(0x100, org)
    app.orgs_list.addItem(item)
    app.orgs_list.setCurrentRow(0)
    # The currentRowChanged signal may have fired before _orgs was set;
    # force a re-render.
    app._update_org_details(0)


def _seed_mdm(app) -> None:
    """Drive the MDM tab into its populated state without a real device."""
    sample = {
        "profiles": [
            type("P", (), {
                "display_name": "Acme MDM Profile",
                "identifier": "com.acme.mdm.profile",
                "is_managed": True,
                "is_removable": False,
            })(),
            type("P", (), {
                "display_name": "T-Mobile Hotspot Config",
                "identifier": "com.apple.MobileAsset.HotspotConfiguration.t-mobile",
                "is_managed": True,
                "is_removable": True,
            })(),
            type("P", (), {
                "display_name": "Wi-Fi",
                "identifier": "com.apple.wifi.managed",
                "is_managed": True,
                "is_removable": False,
            })(),
        ],
        "apps": [
            type("A", (), {
                "name": "Settings",
                "bundle_identifier": "com.apple.Preferences",
                "short_version": "5.5",
                "version": "5500",
                "static_disk_usage": 12_000_000,
                "dynamic_disk_usage": 800_000,
                "application_type": "System",
            })(),
            type("A", (), {
                "name": "Acme Inventory",
                "bundle_identifier": "com.acme.inventory.scanner",
                "short_version": "2.3.1",
                "version": "231",
                "static_disk_usage": 45_000_000,
                "dynamic_disk_usage": 1_200_000,
                "application_type": "User",
            })(),
            type("A", (), {
                "name": "Safari",
                "bundle_identifier": "com.apple.mobilesafari",
                "short_version": "17.5",
                "version": "17520",
                "static_disk_usage": 78_000_000,
                "dynamic_disk_usage": 3_400_000,
                "application_type": "System",
            })(),
        ],
        "network": {
            "WiFi": {"SSID": "Acme-Corp", "Security": "WPA3-Enterprise"},
            "BluetoothAddress": "AA:BB:CC:DD:EE:FF",
            "Carrier": "T-Mobile",
        },
        "security": {
            "ActivationLock": False,
            "Bricked": False,
            "IsSupervised": True,
            "MDMEnrollment": "Enrolled",
        },
        "certificates": [
            type("C", (), {"name": "Acme MDM Identity", "uuid": "ABCD-1234"})(),
            type("C", (), {"name": "Apple Pay", "uuid": "EFGH-5678"})(),
        ],
    }
    mdm = app.mdm_tab_controller
    # Drive on_device_changed so the visibility transitions fire the
    # way they would in the real app.
    fake_dev = app.devices_list.currentItem().data(0x100)
    mdm.on_device_changed(fake_dev)
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    mdm._populate(sample)
    mdm.status_label.setText(
        f"Last refresh: {len(sample['profiles'])} profiles, "
        f"{len(sample['apps'])} apps, {len(sample['certificates'])} certs"
    )


# --- Render loop ---


def _grab(app, out_path: Path, size: tuple[int, int] = (1100, 750)) -> None:
    """Resize, render, save the current tab."""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QApplication

    app.resize(QSize(*size))
    QApplication.processEvents()
    pixmap = app.grab()
    pixmap.save(str(out_path))
    print(f"rendered: {out_path}  size={pixmap.size().width()}x{pixmap.size().height()}")


def _switch_tab(app, tab_widget) -> None:
    from PySide6.QtWidgets import QApplication

    app.tabs.setCurrentWidget(tab_widget)
    QApplication.processEvents()


def main(out_dir: str = "/tmp") -> None:
    from PySide6.QtWidgets import QApplication

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Ensure a QApplication exists for the duration of the script.
    QApplication.instance() or QApplication(sys.argv)

    _patch_environment()

    # Build the app. After this, all five tabs exist as widgets.
    from apple_device_cli.gui_qt import EnrollmentApp

    win = EnrollmentApp()
    win.show()
    QApplication.processEvents()

    # Empty states first (before any seeding) so we capture the no-data
    # layout cleanly.
    labels = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    print(f"tab labels: {labels}")

    # Devices empty state.
    _switch_tab(win, win.devices_tab)
    _grab(win, out / "tab-devices-empty.png")

    # Organizations empty state.
    _switch_tab(win, win.orgs_tab)
    _grab(win, out / "tab-organizations-empty.png")

    # Enrollment: just renders. (No data to seed; the tab shows controls.)
    _switch_tab(win, win.enroll_tab)
    _grab(win, out / "tab-enrollment.png")

    # Restore: just renders. (Same — the tab is control-heavy.)
    _switch_tab(win, win.restore_tab)
    _grab(win, out / "tab-restore.png")

    # MDM empty state.
    _switch_tab(win, win.mdm_tab)
    _grab(win, out / "tab-mdm-empty.png")

    # --- Now seed and re-render the populated states ---
    _seed_devices(win)
    _seed_orgs(win)

    # Devices populated.
    _switch_tab(win, win.devices_tab)
    QApplication.processEvents()
    _grab(win, out / "tab-devices-render.png")

    # Organizations populated.
    _switch_tab(win, win.orgs_tab)
    QApplication.processEvents()
    _grab(win, out / "tab-organizations-render.png")

    # MDM populated.
    _seed_mdm(win)
    _switch_tab(win, win.mdm_tab)
    QApplication.processEvents()
    _grab(win, out / "tab-mdm-render.png")

    print("done.")


if __name__ == "__main__":
    main(out_dir=sys.argv[1] if len(sys.argv) > 1 else "/tmp")