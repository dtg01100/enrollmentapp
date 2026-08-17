"""Tests for the auto-refresh QTimer in MainWindow.

Round 3 step 10: MainWindow installs a QTimer that periodically calls
_refresh_devices + _refresh_orgs so a freshly-attached device shows up
without the user clicking Refresh. Skipped while workers are running.
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

from PySide6.QtWidgets import QApplication  # noqa: E402

from apple_device_cli.device.info import DeviceInfo  # noqa: E402
from apple_device_cli.orgs.manager import OrganizationManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Single QApplication for the whole test session.

    Finalizer quits the application and drains pending events so pytest
    can exit cleanly when this file is run in isolation. Without it,
    ``pytest tests/test_gui_auto_refresh.py`` hangs after the test
    summary line because the auto-refresh QTimer keeps firing on the
    QApplication's pending-event queue.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    app.quit()
    app.processEvents()


@pytest.fixture
def sample_devices() -> list[DeviceInfo]:
    return [
        DeviceInfo(
            udid="00008101-001234567890ABCD",
            device_name="Test iPhone",
            device_type="iPhone14,2",
            firmware_version="17.0",
            build_version="21A329",
            ecid="0x1234",
        )
    ]


@pytest.fixture
def make_app(qapp, tmp_path, monkeypatch):
    from apple_device_cli.gui_qt import EnrollmentApp

    def _factory(orgs=None):
        from apple_device_cli import gui_qt

        class _Signal:
            def __init__(self):
                self._slots = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self, *args):
                for slot in list(self._slots):
                    slot(*args)

        class SyncWorker:
            def __init__(self, fn):
                self.fn = fn
                self.result = None
                self.error = None
                self.completed = _Signal()
                self.finished = _Signal()

            def start(self):
                try:
                    self.result = self.fn()
                except Exception as exc:
                    self.error = exc
                self.completed.emit(self.result, self.error)
                self.finished.emit()

            def quit(self):
                pass

            def wait(self, timeout=0):
                return True

        monkeypatch.setattr(gui_qt, "WorkerThread", SyncWorker)

        original_init = OrganizationManager.__init__

        def patched_init(self, orgs_dir=None):
            original_init(self, orgs_dir=tmp_path)

        from unittest.mock import patch

        with patch.object(OrganizationManager, "__init__", patched_init):
            with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
                with patch(
                    "apple_device_cli.gui_qt.OrganizationManager.list_orgs",
                    return_value=orgs or [],
                ):
                    return gui_qt.EnrollmentApp()

    return _factory


class TestAutoRefreshTimerInstalled:
    def test_timer_attribute_present(self, make_app):
        app = make_app()
        assert hasattr(app, "_auto_refresh_timer")
        assert app._auto_refresh_timer is not None

    def test_timer_is_running(self, make_app):
        app = make_app()
        assert app._auto_refresh_timer.isActive() is True

    def test_default_interval_is_five_seconds(self, make_app):
        app = make_app()
        # 5 s default = 5000 ms
        assert app._auto_refresh_timer.interval() == 5000

    def test_tick_invokes_refresh_devices(self, make_app, monkeypatch):
        app = make_app()
        called = {"devices": 0, "orgs": 0}
        monkeypatch.setattr(
            app, "_refresh_devices", lambda: called.__setitem__("devices", called["devices"] + 1)
        )
        monkeypatch.setattr(
            app, "_refresh_orgs", lambda: called.__setitem__("orgs", called["orgs"] + 1)
        )
        app._auto_refresh_tick()
        assert called["devices"] == 1
        assert called["orgs"] == 1

    def test_tick_skipped_when_workers_active(self, make_app, monkeypatch):
        """Auto-refresh must not race an in-flight worker."""
        app = make_app()
        # Pretend a worker is running
        app._worker_pool._workers.append(object())
        called = {"devices": 0}
        monkeypatch.setattr(
            app, "_refresh_devices", lambda: called.__setitem__("devices", called["devices"] + 1)
        )
        app._auto_refresh_tick()
        assert called["devices"] == 0

    def test_tick_handles_refresh_failure(self, make_app, monkeypatch):
        """Auto-refresh failures are non-fatal — exception swallowed."""
        app = make_app()

        def boom():
            raise RuntimeError("simulated")

        monkeypatch.setattr(app, "_refresh_devices", boom)
        # Should not raise
        app._auto_refresh_tick()