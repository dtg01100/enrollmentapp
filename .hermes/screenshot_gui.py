"""Render screenshots of each GUI tab for review.

Launches the GUI under the offscreen Qt platform, populates each tab with
realistic state (devices, orgs, firmware cache, signed versions), and
captures one PNG per tab. Saves under .hermes/screenshots/.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

# Use src layout
sys.path.insert(0, "src")

from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.gui_qt import EnrollmentApp
from apple_device_cli.orgs.manager import Organization


def gen_self_signed_cert(out_path: Path, days_until_expiry: int) -> datetime:
    """Generate a self-signed DER cert valid for `days_until_expiry` days."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "test")]
    )
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=days_until_expiry)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(expiry)
        .sign(key, hashes.SHA256())
    )
    out_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    # Also write a key so the org has a full identity
    key_path = out_path.with_name("key.der")
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return expiry


def main() -> int:
    out_dir = Path(".hermes/screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)

    # Build the EnrollmentApp against a clean tmp orgs dir
    import tempfile
    tmp_orgs = Path(tempfile.mkdtemp(prefix="ios-enroll-gui-shots-"))
    monkey_orgs_dir = tmp_orgs

    from apple_device_cli.orgs.manager import OrganizationManager
    original_default = OrganizationManager.__init__

    def patched_init(self, orgs_dir=None):
        original_default(self, orgs_dir=monkey_orgs_dir)

    import unittest.mock as mock
    with mock.patch.object(OrganizationManager, "__init__", patched_init):
        # Seed orgs
        mgr = OrganizationManager()
        # Healthy org (🟢)
        healthy_cert = tmp_orgs / "healthy_cert.der"
        gen_self_signed_cert(healthy_cert, days_until_expiry=365 * 3)
        org1 = Organization(
            name="Capital Candy Company",
            org_id="com.capitalcandy",
            mdm_url="https://mdm.capitalcandy.com/mdm",
            checkin_url="https://mdm.capitalcandy.com/checkin",
            mdm_topic="com.capitalcandy.mdm",
            cert_path=str(healthy_cert),
            key_path=str(healthy_cert.with_name("key.der")),
            created_at="2024-01-15T10:30:00",
        )
        mgr.save_org(org1, overwrite=True)

        # Expiring soon org (🟡)
        soon_cert = tmp_orgs / "soon_cert.der"
        gen_self_signed_cert(soon_cert, days_until_expiry=14)
        org2 = Organization(
            name="Acme Corp (renewing soon)",
            org_id="com.acme",
            mdm_url="https://acme.mdm.example.com/mdm",
            cert_path=str(soon_cert),
            key_path=str(soon_cert.with_name("key.der")),
            created_at="2023-12-01T08:00:00",
        )
        mgr.save_org(org2, overwrite=True)

        # No-identity org (⚪)
        org3 = Organization(
            name="Beta Industries",
            org_id="com.beta",
            mdm_url="https://beta.mdm.example.com/mdm",
            checkin_url="https://beta.mdm.example.com/checkin",
            created_at="2026-07-22T14:15:00",
        )
        mgr.save_org(org3, overwrite=True)

        # Build the app
        win = EnrollmentApp()
        win.resize(QSize(1200, 780))

        # Seed devices — but ONLY for the Devices/Enroll tabs. The
        # Restore-tab screenshot needs to show the empty state, so we'll
        # clear the device combo on that tab.
        win._devices = [
            DeviceInfo(
                udid="00008101-001234567890ABCD",
                device_name="Test iPhone (Normal)",
                device_type="iPhone14,2",
                firmware_version="17.0",
                build_version="21A329",
                ecid="0x1234",
            ),
            DeviceInfo(
                udid="00008110-00ABCDEF12345678",
                device_name="Test iPad",
                device_type="iPad13,4",
                firmware_version="17.1",
                build_version="21B74",
                ecid="0x5678",
            ),
        ]

        # Use _on_devices_refreshed so empty-state placeholders + status bar update.
        # monkeypatch list_devices so it returns our seeded devices.
        from unittest.mock import patch as mock_patch
        with mock_patch(
            "apple_device_cli.gui_qt.list_devices",
            return_value=list(win._devices),
        ):
            win._on_devices_refreshed(list(win._devices), None, token=win._request_token)

        # Seed orgs by calling _on_orgs_refreshed (so empty-state + details pane update).
        with mock_patch(
            "apple_device_cli.gui_qt.OrganizationManager.list_orgs",
            return_value=list(win._orgs),
        ):
            win._on_orgs_refreshed(list(win._orgs), None, token=win._request_token)
        # Select the first org so the details pane populates for the screenshot.
        win.orgs_list.setCurrentRow(0)
        app.processEvents()

        # For the Enrollment-tab screenshot, switch to the second org
        # (Acme Corp — has an expiring cert) so the cert-expiry banner
        # shows. Saves a pre-seeded last-op so the status bar shows it.
        from PySide6.QtCore import QSettings
        QSettings("ios-enroll", "gui").setValue(
            "lastOperation", "Restored iPad_Pro_26.6_23G71_Restore.ipsw @ 19:26"
        )

        # Add a few log lines so the bottom panel has content
        win._log("Found 2 device(s).")
        win._log("Found 3 organization(s).")
        win._log("GUI initialized. Connect an iOS device to begin.")

        # Update status bar
        win._update_status_bar()

        # Process events to let widgets lay out
        app.processEvents()

        # Screenshot helper
        def grab(tab_index: int, name: str, *, force_empty: bool = False) -> Path:
            win.tabs.setCurrentIndex(tab_index)
            if force_empty:
                # Clear device combo so the empty-state hint shows
                win.restore_device_combo.clear()
                win._restore_ipsw_path = None
                win._update_restore_empty_state()
            # For the Enrollment tab, pick the org with an expiring cert
            # (Acme Corp, index 1) so the cert-expiry banner shows.
            if tab_index == 2 and win.enroll_org_combo.count() > 1:
                win.enroll_org_combo.setCurrentIndex(1)
                app.processEvents()
            app.processEvents()
            pix: QPixmap = win.grab()
            out = out_dir / f"{name}.png"
            pix.save(str(out))
            return out

        paths = [
            grab(0, "01-devices"),
            grab(1, "02-orgs"),
            grab(2, "03-enroll"),
            grab(3, "04-restore", force_empty=True),
        ]

        # Full-window composite
        win.tabs.setCurrentIndex(0)
        app.processEvents()
        win.grab().save(str(out_dir / "00-overview.png"))

        # Resize down for the chat-friendly version
        for p in paths:
            img = Image.open(p)
            # Cap at 1400px wide
            if img.width > 1400:
                ratio = 1400 / img.width
                img = img.resize(
                    (1400, int(img.height * ratio)),
                    Image.Resampling.LANCZOS,
                )
            img.save(p)

        # Same for overview
        img = Image.open(out_dir / "00-overview.png")
        if img.width > 1400:
            ratio = 1400 / img.width
            img = img.resize(
                (1400, int(img.height * ratio)),
                Image.Resampling.LANCZOS,
            )
            img.save(out_dir / "00-overview.png")

    for p in paths:
        print(f"  {p}  ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
