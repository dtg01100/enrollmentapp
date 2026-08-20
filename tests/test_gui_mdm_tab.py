"""MDMTab controller tests.

Verifies the 5th tab (MDM) instantiates cleanly through the MainWindow
back-compat shims (``app.mdm_*`` widgets), surfaces the empty state
when no device is selected, and correctly switches to the populated
content area when a device is picked.

Tests run against real PySide6 widgets under ``QT_QPA_PLATFORM=offscreen``
so no display is required in CI.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMessageBox,
    QTableWidget,
    QTextEdit,
)

from apple_device_cli.device.info import DeviceInfo  # noqa: E402
from apple_device_cli.gui_qt.mdm_tab import MDMTab  # noqa: E402
from apple_device_cli.gui_qt.tabs import TabController  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    app.quit()
    app.processEvents()


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """Modal dialogs block the offscreen Qt test runner forever."""
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    yield


@pytest.fixture
def fake_device() -> DeviceInfo:
    return DeviceInfo(
        udid="00008101-001234567890ABCD",
        device_name="Test iPhone",
        device_type="iPhone14,2",
        firmware_version="17.0",
        build_version="21A329",
        ecid="0x1234",
    )


# ---------------------------------------------------------------------------
# TabController conformance
# ---------------------------------------------------------------------------


class TestMDMTabConformance:
    def test_mdm_tab_is_a_tab_controller(self):
        """MDMTab must be a TabController subclass per the task spec."""
        assert issubclass(MDMTab, TabController)

    def test_mdm_tab_implements_all_four_abstract_methods(self):
        for name in ("widget", "refresh", "on_org_changed", "on_device_changed"):
            assert callable(getattr(MDMTab, name)), f"MDMTab missing {name}"


# ---------------------------------------------------------------------------
# App-level wiring (5th tab registered in MainWindow)
# ---------------------------------------------------------------------------


class TestMDMTabRegistration:
    def test_main_window_has_five_tabs(self, make_app):
        app = make_app()
        assert app.tabs.count() == 5

    def test_mdm_tab_is_the_fifth_tab(self, make_app):
        app = make_app()
        labels = [app.tabs.tabText(i) for i in range(app.tabs.count())]
        assert labels == ["Devices", "Organizations", "Enrollment", "Restore", "MDM"]

    def test_mdm_widgets_are_mirrored_on_app(self, make_app):
        """app.mdm_<widget> back-compat shims resolve to real QWidgets."""
        app = make_app()
        assert isinstance(app.mdm_refresh_btn, type(app.refresh_devices_btn))
        assert isinstance(app.mdm_profiles_table, QTableWidget)
        assert isinstance(app.mdm_apps_table, QTableWidget)
        assert isinstance(app.mdm_network_view, QTextEdit)
        assert isinstance(app.mdm_security_view, QTextEdit)
        assert isinstance(app.mdm_certs_view, QTextEdit)

    def test_mdm_controller_is_mirrored_on_app(self, make_app):
        app = make_app()
        assert isinstance(app.mdm_tab_controller, MDMTab)
        assert app.mdm_tab is app.mdm_tab_controller.tab_widget()


# ---------------------------------------------------------------------------
# Empty state when no device is selected
# ---------------------------------------------------------------------------


class TestMDMEmptyState:
    def test_initial_state_shows_empty_message(self, make_app):
        """Right after launch: empty-state label visible, content hidden,
        refresh disabled."""
        app = make_app()
        mdm = app.mdm_tab_controller
        # Switch to the MDM tab so the widget is actually realized
        # under the offscreen platform (otherwise isVisibleTo checks
        # against the parent chain return False for the hidden tab).
        app.tabs.setCurrentWidget(app.mdm_tab)
        QApplication.processEvents()

        assert mdm.empty_state_label.isHidden() is False
        assert "Select a device" in mdm.empty_state_label.text()
        assert mdm._content_widget.isHidden() is True
        assert mdm.refresh_btn.isEnabled() is False
        assert mdm.status_label.text() == "(no device)"

    def test_on_device_changed_none_renders_empty(self, make_app):
        """Clearing the device selection puts the tab back into empty state."""
        app = make_app()
        mdm = app.mdm_tab_controller
        app.tabs.setCurrentWidget(app.mdm_tab)
        QApplication.processEvents()

        # Drive directly — no need to involve the device list selection.
        mdm.on_device_changed(None)
        QApplication.processEvents()
        assert mdm.empty_state_label.isHidden() is False
        assert mdm._content_widget.isHidden() is True
        assert mdm.refresh_btn.isEnabled() is False
        assert mdm.status_label.text() == "(no device)"

    def test_refresh_button_disabled_without_device(self, make_app):
        """The Refresh button is disabled until a device is picked."""
        app = make_app()
        mdm = app.mdm_tab_controller
        app.tabs.setCurrentWidget(app.mdm_tab)
        QApplication.processEvents()
        assert mdm.refresh_btn.isEnabled() is False

    def test_trigger_refresh_without_device_renders_empty(
        self, make_app, monkeypatch
    ):
        """If the user clicks Refresh with no device, surface empty state
        and do not invoke the underlying WorkerThread pool."""
        app = make_app()
        mdm = app.mdm_tab_controller
        # Mock the selected device to None
        monkeypatch.setattr(mdm._shell, "_selected_device", lambda: None)

        submit_called = MagicMock()
        monkeypatch.setattr(
            app._worker_pool, "submit", submit_called, raising=False,
        )

        mdm._trigger_refresh()
        QApplication.processEvents()

        assert mdm.empty_state_label.isHidden() is False
        assert mdm._content_widget.isHidden() is True
        assert mdm.refresh_btn.isEnabled() is False
        submit_called.assert_not_called()


# ---------------------------------------------------------------------------
# Device selection switches the tab to the populated content area
# ---------------------------------------------------------------------------


class TestMDMDeviceSelection:
    def test_on_device_changed_shows_content(
        self, make_app, fake_device, monkeypatch
    ):
        """When a device is selected, content is shown and refresh is
        enabled. We don't actually start the worker (no real device)
        so we monkeypatch the device lookup and the shell helper."""
        app = make_app()
        mdm = app.mdm_tab_controller
        app.tabs.setCurrentWidget(app.mdm_tab)
        QApplication.processEvents()

        # Make the shell report our device and prevent any real
        # network call (the worker would try to open lockdown).
        monkeypatch.setattr(mdm._shell, "_selected_device", lambda: fake_device)

        # Replace _run_worker with a no-op so the test doesn't try
        # to spawn a real QThread against a fake device.
        monkeypatch.setattr(mdm, "_run_worker", lambda udid: None)

        mdm.on_device_changed(fake_device)
        QApplication.processEvents()

        # Content is shown, empty state hidden, refresh re-enabled.
        assert mdm._content_widget.isHidden() is False
        assert mdm.empty_state_label.isHidden() is True
        # _current_udid is tracked for the worker call
        assert mdm._current_udid == fake_device.udid

    def test_refresh_in_flight_debounces(
        self, make_app, fake_device, monkeypatch
    ):
        """Rapid refresh clicks while a worker is running do not start
        a second worker — instead, a single follow-up refresh is queued
        and runs after the in-flight one completes."""
        app = make_app()
        mdm = app.mdm_tab_controller
        app.tabs.setCurrentWidget(app.mdm_tab)
        QApplication.processEvents()
        monkeypatch.setattr(mdm._shell, "_selected_device", lambda: fake_device)

        calls = []
        monkeypatch.setattr(mdm, "_run_worker", lambda udid: calls.append(udid))

        mdm.on_device_changed(fake_device)
        mdm._trigger_refresh()
        mdm._trigger_refresh()
        mdm._trigger_refresh()
        # The first call comes from on_device_changed; the subsequent
        # _trigger_refresh calls should NOT have spawned new workers.
        assert calls == [fake_device.udid]


# ---------------------------------------------------------------------------
# Refresh / populate flow (data flow without real device)
# ---------------------------------------------------------------------------


class TestMDMPopulate:
    def test_populate_fills_profiles_and_apps_tables(self, make_app):
        """``_populate`` writes rows into the QTableWidgets based on the
        inspection result dict."""
        from apple_device_cli.device.mdm_inspect import (
            AppInfo,
            ProfileInfo,
        )

        app = make_app()
        mdm = app.mdm_tab_controller

        data = {
            "profiles": [
                ProfileInfo(
                    identifier="com.example.mdm",
                    display_name="Work MDM",
                    is_managed=True,
                    is_removable=False,
                ),
                ProfileInfo(
                    identifier="com.example.wifi",
                    display_name="Office WiFi",
                    is_managed=False,
                    is_removable=True,
                ),
            ],
            "apps": [
                AppInfo(
                    bundle_identifier="com.example.app",
                    name="Example",
                    short_version="1.2.3",
                    static_disk_usage=1024,
                    dynamic_disk_usage=2048,
                    application_type="User",
                ),
                AppInfo(
                    bundle_identifier="com.apple.springboard",
                    name="SpringBoard",
                    application_type="System",
                ),
            ],
            "network": {"ssid": "AcmeCorp", "bssid": "00:11:22:33:44:55", "rssi": -55},
            "security": {
                "is_passcode_set": True,
                "device_class": "iPhone",
                "battery_current_capacity": 87,
            },
            "certificates": [],
        }

        mdm._populate(data)

        # Profiles
        assert mdm.profiles_table.rowCount() == 2
        assert mdm.profiles_table.item(0, 0).text() == "Work MDM"
        assert mdm.profiles_table.item(0, 1).text() == "com.example.mdm"
        assert mdm.profiles_table.item(0, 2).text() == "Yes"  # is_managed
        assert mdm.profiles_table.item(0, 3).text() == "No"   # not is_removable
        assert mdm.profiles_table.item(1, 0).text() == "Office WiFi"
        assert mdm.profiles_table.item(1, 2).text() == "No"   # not is_managed
        assert mdm.profiles_table.item(1, 3).text() == "Yes"  # is_removable

        # Apps
        assert mdm.apps_table.rowCount() == 2
        assert mdm.apps_table.item(0, 0).text() == "Example"
        assert mdm.apps_table.item(0, 1).text() == "com.example.app"
        assert mdm.apps_table.item(0, 2).text() == "1.2.3"
        assert mdm.apps_table.item(0, 4).text() == "User"
        # Total size = 1024 + 2048 = 3072 bytes => 3.0 KB
        assert "KB" in mdm.apps_table.item(0, 3).text()
        assert mdm.apps_table.item(1, 0).text() == "SpringBoard"
        assert mdm.apps_table.item(1, 4).text() == "System"

        # Info panels — JSON-rendered
        assert "AcmeCorp" in mdm.network_view.toPlainText()
        assert "iPhone" in mdm.security_view.toPlainText()
        # No certs → placeholder text
        assert "no provisioning profiles" in mdm.certs_view.toPlainText().lower()

    def test_populate_handles_empty_lists(self, make_app):
        """Empty profile/app lists produce zero-row tables without crashing."""
        app = make_app()
        mdm = app.mdm_tab_controller
        mdm._populate(
            {
                "profiles": [],
                "apps": [],
                "network": {},
                "security": {},
                "certificates": [],
            }
        )
        assert mdm.profiles_table.rowCount() == 0
        assert mdm.apps_table.rowCount() == 0
        # Empty dicts => "(no data)" placeholder
        assert mdm.network_view.toPlainText() == "(no data)"
        assert mdm.security_view.toPlainText() == "(no data)"

    def test_populate_renders_certs(self, make_app):
        """When certs are present, they show in the certs view."""
        from apple_device_cli.device.mdm_inspect import CertificateInfo

        app = make_app()
        mdm = app.mdm_tab_controller
        mdm._populate(
            {
                "profiles": [],
                "apps": [],
                "network": {},
                "security": {},
                "certificates": [
                    CertificateInfo(
                        uuid="11111111-1111-1111-1111-111111111111",
                        name="Acme Dev Profile",
                    ),
                ],
            }
        )
        text = mdm.certs_view.toPlainText()
        assert "Acme Dev Profile" in text
        assert "11111111-1111-1111-1111-111111111111" in text


# ---------------------------------------------------------------------------
# Refresh-done callback
# ---------------------------------------------------------------------------


class TestMDMRefreshDone:
    def test_refresh_done_error_sets_status(self, make_app):
        app = make_app()
        mdm = app.mdm_tab_controller
        mdm._refresh_done(None, error=RuntimeError("boom"))
        assert "Refresh failed" in mdm.status_label.text()
        assert "boom" in mdm.status_label.text()
        assert mdm.refresh_btn.isEnabled() is True

    def test_refresh_done_none_result(self, make_app):
        app = make_app()
        mdm = app.mdm_tab_controller
        mdm._refresh_done(None, error=None)
        assert mdm.status_label.text() == "(no data)"

    def test_refresh_done_populates_and_reports_counts(self, make_app):
        from apple_device_cli.device.mdm_inspect import (
            AppInfo,
            ProfileInfo,
        )

        app = make_app()
        mdm = app.mdm_tab_controller
        mdm._refresh_done(
            {
                "profiles": [
                    ProfileInfo(identifier="a", display_name="A"),
                    ProfileInfo(identifier="b", display_name="B"),
                ],
                "apps": [
                    AppInfo(bundle_identifier="a", name="A"),
                ],
                "certificates": [],
                "network": {},
                "security": {},
            },
            error=None,
        )
        assert "2 profiles" in mdm.status_label.text()
        assert "1 apps" in mdm.status_label.text()
        assert "0 certs" in mdm.status_label.text()

    def test_refresh_done_pending_rerun(self, make_app, fake_device, monkeypatch):
        """If a refresh was queued while one was in flight, it runs after
        the in-flight one completes."""
        app = make_app()
        mdm = app.mdm_tab_controller
        monkeypatch.setattr(mdm._shell, "_selected_device", lambda: fake_device)

        calls = []
        monkeypatch.setattr(mdm, "_run_worker", lambda udid: calls.append(udid))

        mdm._refresh_in_flight = True
        mdm._pending_refresh = True
        mdm._refresh_done({"profiles": [], "apps": [], "certificates": []}, error=None)
        # The pending refresh was drained, not the in-flight one.
        assert calls == [fake_device.udid]
        assert mdm._refresh_in_flight is True
        assert mdm._pending_refresh is False
