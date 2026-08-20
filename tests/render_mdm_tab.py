"""Headless render of MDMTab for layout verification.

Loads MDMTab into a fake shell with a fake device + sample populated
data, then grabs the rendered widget as a PNG so we can audit layout
without needing a real iPad or the full GUI running.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from apple_device_cli.gui_qt.mdm_tab import MDMTab


@dataclass
class FakeDevice:
    udid: str = "FAKE-UDID-0001"
    device_name: str = "iPad (Test)"
    ios_version: str = "17.5"
    model: str = "iPad7,11"
    paired: bool = True


@dataclass
class FakeShell:
    devices_list = None
    devices: list = field(default_factory=lambda: [FakeDevice()])

    def _selected_device(self) -> FakeDevice | None:
        return self.devices[0] if self.devices else None

    def _run_worker(self, worker, callback, widgets_to_disable=None) -> None:
        # Synchronously invoke the worker for headless rendering.
        try:
            result = worker._target() if hasattr(worker, "_target") else worker.run()
        except Exception as exc:
            callback(None, exc)
        else:
            callback(result, None)


def main(out_dir: str = "/tmp") -> None:
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)

    shell = FakeShell()
    tab = MDMTab(shell)

    # Inject sample data directly so we can see populated state.
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

    # Simulate the "device selected" state and populate. Drive the same
    # visibility transitions the real app path would, so the render
    # matches what the user sees after picking a device on the Devices
    # tab and waiting for the worker to finish.
    tab.on_device_changed(shell._selected_device())
    app.processEvents()
    tab._populate(sample)
    tab.status_label.setText(
        f"Last refresh: {len(sample['profiles'])} profiles, "
        f"{len(sample['apps'])} apps, {len(sample['certificates'])} certs"
    )

    widget = tab.widget()
    widget.resize(QSize(1100, 750))
    widget.show()
    app.processEvents()

    # Grab the rendered widget.
    pixmap = widget.grab()
    out_path = Path(out_dir) / "mdm-tab-render.png"
    pixmap.save(str(out_path))
    print(f"rendered: {out_path}  size={pixmap.size().width()}x{pixmap.size().height()}")

    # Also render the empty state for comparison.
    tab._render_empty("Select a device to inspect profiles, apps, and info.")
    app.processEvents()
    empty_pixmap = widget.grab()
    empty_path = Path(out_dir) / "mdm-tab-empty.png"
    empty_pixmap.save(str(empty_path))
    print(f"rendered: {empty_path}  size={empty_pixmap.size().width()}x{empty_pixmap.size().height()}")


if __name__ == "__main__":
    main(out_dir=sys.argv[1] if len(sys.argv) > 1 else "/tmp")
