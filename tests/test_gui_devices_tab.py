"""DevicesTab controller tests.

Round 3 step 12: per-tab test split. Devices-related tests moved out
of tests/test_gui_qt.py. Shared fixtures (make_app, sample_devices,
sample_org, qapp, _no_blocking_dialogs) come from
``tests.gui_fixtures``.

Tests here exercise DevicesTab through the MainWindow back-compat
shims (app.devices_list, app.refresh_devices_btn, etc.) so the
controller's behavior is verified in the same wiring production code
uses.
"""
from __future__ import annotations

from unittest.mock import MagicMock


from apple_device_cli.device.info import DeviceInfo


class TestDevicesContextMenu:
    def test_build_devices_context_menu_has_three_actions_when_no_org(
        self, make_app
    ):
        """No org selected → 'Make Supervised' is hidden (gating)."""
        app = make_app()
        app.devices_list.setCurrentRow(0)

        menu = app._build_devices_context_menu()
        labels = [a.text() for a in menu.actions()]
        assert "Show Device Info" in labels
        assert "Activate" in labels
        assert "Pair / Trust" in labels
        assert "Make Supervised" not in labels
        menu.deleteLater()

    def test_build_devices_context_menu_includes_make_supervised_with_org(
        self, make_app, sample_org
    ):
        """With an org → 'Make Supervised' is visible."""
        app = make_app(orgs=[sample_org])

        # Force gating state to reflect org presence
        app._gating.set_org(sample_org)
        # Add a device to the list
        fake_device = MagicMock(spec=DeviceInfo, udid="udid-x")
        app._devices.append(fake_device)
        from PySide6.QtWidgets import QListWidgetItem

        QListWidgetItem("test-device  (udid-x)", app.devices_list)
        app.devices_list.setCurrentRow(0)
        app._gating.set_device(fake_device)

        menu = app._build_devices_context_menu()
        labels = [a.text() for a in menu.actions()]
        assert "Make Supervised" in labels
        menu.deleteLater()


class TestRefreshDevicesTab:
    def test_refresh_devices_happy_path(self, make_app, monkeypatch):
        """list_devices returns devices → _devices populated, list shown."""
        app = make_app()
        fake_devices = [
            MagicMock(spec=DeviceInfo, udid="udid-1", device_name="iPhone A"),
            MagicMock(spec=DeviceInfo, udid="udid-2", device_name="iPhone B"),
        ]
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.list_devices", lambda: fake_devices
        )

        app._refresh_devices()

        assert len(app._devices) == 2
        assert app.devices_list.count() == 2
        assert "Found 2 device(s)" in app.log_text.toPlainText()

    def test_refresh_devices_skipped_when_stale_token(
        self, make_app, monkeypatch
    ):
        """A stale completion does not overwrite newer data."""
        app = make_app()
        fake_devices = [
            MagicMock(spec=DeviceInfo, udid="udid-fresh", device_name="iPhone X"),
        ]
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.list_devices", lambda: fake_devices
        )

        # Manually simulate a stale token completion
        app._on_devices_refreshed([], None, token=-1)
        # The _devices list should not have been overwritten
        assert app._devices == []