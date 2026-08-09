"""Tests for the Restore tab in the PySide6 GUI.

The tab is populated lazily (PySide6 is an optional ``[gui]`` extra), so the
whole module is skipped when PySide6 isn't installed. The offscreen Qt
platform plugin makes the tests run without a display.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from apple_device_cli.device.info import DeviceInfo  # noqa: E402
from apple_device_cli.restore.engine import ProgressEvent, ProgressUpdate  # noqa: E402
from apple_device_cli.restore.engine import VerifyResult  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    """Single QApplication for the whole module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def sync_workers(monkeypatch):
    """Replace gui_qt.WorkerThread with a synchronous fake.

    The Restore tab's handlers are invoked through ``_run_worker`` which
    relies on signal delivery that never fires without an event loop in the
    test thread. A synchronous worker runs the callable on the caller's
    thread, so init's refresh flows and the version/restore handlers execute
    deterministically.
    """

    class _Signal:
        def __init__(self) -> None:
            self._slots: list = []

        def connect(self, slot) -> None:
            self._slots.append(slot)

        def emit(self, *args) -> None:
            for slot in list(self._slots):
                slot(*args)

    class SyncWorker:
        def __init__(self, fn):
            self.fn = fn
            self.result = None
            self.error: Exception | None = None
            self.completed = _Signal()
            self.finished = _Signal()

        def start(self) -> None:
            try:
                self.result = self.fn()
            except Exception as exc:  # noqa: BLE001
                self.error = exc
            self.completed.emit(self.result, self.error)
            self.finished.emit()

        def quit(self) -> None:
            pass

        def wait(self, timeout: int = 0) -> bool:
            return True

    import apple_device_cli.gui_qt as gui_qt

    monkeypatch.setattr(gui_qt, "WorkerThread", SyncWorker)
    return SyncWorker


@pytest.fixture
def make_app(qapp, tmp_path, monkeypatch, sync_workers):
    """Factory for an EnrollmentApp isolated from the real machine.

    Redirects the cache dir to a tmp dir (so ``_create_restore_tab`` and
    ``resolve_cache_dir`` don't touch the real home), mocks device and org
    enumeration to empty, and patches ``OrganizationManager`` so org
    refresh is a no-op.

    Also hermetic against a live machine: ``detect_recovery_devices_present``
    defaults to False (no Recovery-mode device on the USB bus) and
    ``recovery_device_descriptor`` to None, so the synthetic "Recovery mode"
    combo entry never appears unless a test explicitly opts in by overriding
    the attribute with ``monkeypatch`` after the fixture runs.
    """
    import apple_device_cli.gui_qt as gui_qt

    monkeypatch.setattr(gui_qt, "resolve_cache_dir", lambda override=None: tmp_path)
    monkeypatch.setattr(gui_qt, "write_cache_config", lambda cache_dir: None)
    monkeypatch.setattr(gui_qt, "cache_state", lambda cache_dir: {
        "path": str(cache_dir),
        "size_bytes": 0,
        "ipsw_count": 0,
        "ipsw_files": [],
    })
    monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: False)
    monkeypatch.setattr(gui_qt, "recovery_device_descriptor", lambda: None)

    class _FakeOrgManager:
        def list_orgs(self) -> list:
            return []

    monkeypatch.setattr(gui_qt, "OrganizationManager", _FakeOrgManager)

    def _factory(devices: list[DeviceInfo] | None = None) -> gui_qt.EnrollmentApp:
        with patch("apple_device_cli.gui_qt.list_devices", return_value=devices or []):
            return gui_qt.EnrollmentApp()

    return _factory


@pytest.fixture
def sample_devices() -> list[DeviceInfo]:
    return [
        DeviceInfo(
            udid="00008101-001234567890ABCD",
            device_name="Test iPad",
            device_type="iPad13,4",
            firmware_version="26.6",
            build_version="23G71",
            ecid="0x1234",
        )
    ]


class TestRestoreTabStructure:
    def test_restore_tab_has_required_widgets(self, qapp, make_app):
        app = make_app()
        app.show()
        qapp.processEvents()

        labels = [app.tabs.tabText(i) for i in range(app.tabs.count())]
        assert "Restore" in labels

        assert app.restore_device_combo is not None
        assert app.restore_product_type_label is not None
        assert app.restore_cache_path_label is not None
        assert app.restore_versions_combo is not None
        assert app.restore_refresh_versions_btn is not None
        assert app.restore_ipsw_path_label is not None
        assert app.restore_start_btn is not None
        assert app.restore_log_text is not None

        # Buttons are gated until a device is selected.
        assert not app.restore_refresh_versions_btn.isEnabled()
        assert not app.restore_start_btn.isEnabled()
        assert app.restore_ipsw_path_label.text() == "<not selected>"

        app.close()

    def test_restore_log_text_is_read_only(self, make_app):
        app = make_app()
        assert app.restore_log_text.isReadOnly()


class TestRestoreDeviceCombo:
    def test_populates_from_devices_and_auto_fills_product_type(self, make_app, sample_devices):
        app = make_app(devices=sample_devices)
        assert app.restore_device_combo.count() == 1
        assert app.restore_device_combo.currentData() == sample_devices[0].udid
        assert app.restore_product_type_label.text() == "iPad13,4"
        assert app.restore_refresh_versions_btn.isEnabled()
        assert not app.restore_start_btn.isEnabled()

    def test_empty_device_list_resets_combo_and_label(self, make_app):
        app = make_app(devices=[])
        assert app.restore_device_combo.count() == 0
        assert app.restore_product_type_label.text() == "<select a device>"
        assert not app.restore_refresh_versions_btn.isEnabled()
        assert not app.restore_start_btn.isEnabled()

    def test_device_change_clears_versions_and_disables_start(self, make_app, sample_devices):
        app = make_app(devices=sample_devices)
        app.restore_versions_combo.addItem("iOS 26.6 (23G71)", userData="http://x/ipsw")
        app.restore_start_btn.setEnabled(True)

        # Selecting index -1 (no device) resets the tab state.
        app.restore_device_combo.setCurrentIndex(-1)

        assert app.restore_versions_combo.count() == 0
        assert not app.restore_start_btn.isEnabled()


class TestRecoveryDeviceInCombo:
    """Recovery-mode devices are invisible to usbmuxd, so the Restore tab's
    combo cannot enumerate them. When one is on the USB bus, a synthetic
    'Recovery mode' entry appears so the recovery buttons + Start can target
    it (the engine resolves the stored SRNM serial to the actual device).
    """

    def test_refresh_devices_shows_recovery_device_when_present(
        self, make_app, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: True)
        monkeypatch.setattr(
            gui_qt,
            "recovery_device_descriptor",
            lambda: ("jxmwm7422v", "00094daa01d80032"),
        )

        app = make_app(devices=[])
        app._populate_restore_device_combo()

        texts = [
            app.restore_device_combo.itemText(i)
            for i in range(app.restore_device_combo.count())
        ]
        assert any("Recovery" in text for text in texts)
        assert app.restore_device_combo.currentData() == "jxmwm7422v"

    def test_no_recovery_device_means_no_extra_entry(self, make_app, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: False)
        app = make_app(devices=[])
        app._populate_restore_device_combo()

        assert app.restore_device_combo.count() == 0


class TestRestoreVersionRefresh:
    def test_refresh_versions_populates_combo_and_enables_start(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        versions = [
            SimpleNamespace(display_label="iOS 26.6 (23G71)", url="http://x/26.6.ipsw"),
            SimpleNamespace(display_label="iOS 26.5.2 (23F84)", url="http://x/26.5.2.ipsw"),
        ]
        monkeypatch.setattr(gui_qt, "list_signed_versions", lambda product_type: versions)

        app = make_app(devices=sample_devices)
        app._refresh_versions()

        assert app.restore_versions_combo.count() == 2
        assert app.restore_versions_combo.currentData() == "http://x/26.6.ipsw"
        assert app.restore_start_btn.isEnabled()
        assert "Found 2 signed version(s)" in app.restore_log_text.toPlainText()

    def test_refresh_versions_error_logged_without_crashing(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        def boom(product_type):
            raise RuntimeError("ipsw tool missing")

        monkeypatch.setattr(gui_qt, "list_signed_versions", boom)

        app = make_app(devices=sample_devices)
        app._refresh_versions()

        assert "ipsw tool missing" in app.restore_log_text.toPlainText()
        assert app.restore_versions_combo.count() == 0

    def test_refresh_versions_falls_back_to_lockdown_product_type(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        # The device list entry lacks a ProductType.
        sample_devices[0].device_type = ""

        versions = [
            SimpleNamespace(display_label="iOS 26.6 (23G71)", url="http://x/26.6.ipsw")
        ]
        monkeypatch.setattr(gui_qt, "list_signed_versions", lambda product_type: versions)
        monkeypatch.setattr(
            gui_qt, "get_product_type_for_udid", lambda udid: "iPad13,4"
        )

        app = make_app(devices=sample_devices)
        app._refresh_versions()

        assert app.restore_product_type_label.text() == "iPad13,4"
        assert app.restore_versions_combo.count() == 1


class TestRestoreStart:
    def test_no_device_warns_and_no_worker(self, make_app, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: False)
        with patch.object(gui_qt.QMessageBox, "warning") as mock_warn:
            app = make_app(devices=[])
            app._start_restore()
        mock_warn.assert_called_once()
        assert len(app._workers) == 0

    def test_no_ipsw_selected_warns_and_no_worker(self, make_app, sample_devices, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        # Neither a version nor a local file is selected.
        with patch.object(gui_qt.QMessageBox, "warning") as mock_warn:
            app._start_restore()
        mock_warn.assert_called_once()
        assert "ipsw" in mock_warn.call_args.args[2].lower()
        assert len(app._workers) == 0

    def test_downloads_and_restores_from_selected_version(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        app.restore_versions_combo.addItem(
            "iOS 26.6 (23G71)", userData="https://example.com/iPad_Pro_26.6_23G71_Restore.ipsw"
        )

        ipsw_path = Path("/tmp/fake.ipsw")
        monkeypatch.setattr(
            gui_qt, "download_ipsw",
            lambda url, dest_dir, progress_callback=None: ipsw_path,
        )

        captured: dict = {}
        fake_result = SimpleNamespace(success=True, error=None, udid="x", ipsw_path=ipsw_path)

        def fake_restore(udid, ipsw_path, cache_dir, progress_callback, ecid=None):
            captured["udid"] = udid
            captured["ipsw_path"] = ipsw_path
            captured["cache_dir"] = cache_dir
            return fake_result

        monkeypatch.setattr(gui_qt, "engine_restore_device", fake_restore)

        app._start_restore()

        assert captured["udid"] == sample_devices[0].udid
        assert captured["ipsw_path"] == ipsw_path
        assert "Restore completed successfully" in app.restore_log_text.toPlainText()

    def test_local_ipsw_is_used_directly(
        self, make_app, sample_devices, tmp_path, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        local = tmp_path / "Local_26.6_23G71_Restore.ipsw"
        local.write_bytes(b"fake")

        app = make_app(devices=sample_devices)
        app._restore_ipsw_path = local
        app.restore_ipsw_path_label.setText(str(local))

        captured: dict = {}
        fake_result = SimpleNamespace(success=True, error=None, udid="x", ipsw_path=local)

        def fake_restore(udid, ipsw_path, cache_dir, progress_callback, ecid=None):
            captured["ipsw_path"] = ipsw_path
            return fake_result

        monkeypatch.setattr(gui_qt, "engine_restore_device", fake_restore)
        # download_ipsw must never run for a local file.
        monkeypatch.setattr(
            gui_qt, "download_ipsw",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not download")),
        )

        app._start_restore()

        assert captured["ipsw_path"] == local

    def test_missing_local_ipsw_warns_and_no_worker(self, make_app, sample_devices, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        app._restore_ipsw_path = Path("/nonexistent/file.ipsw")

        with patch.object(gui_qt.QMessageBox, "warning") as mock_warn:
            app._start_restore()
        mock_warn.assert_called_once()
        assert "not found" in mock_warn.call_args.args[2].lower()
        assert len(app._workers) == 0

    def test_progress_callback_logs_to_restore_panel(
        self, make_app, sample_devices, tmp_path, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        local = tmp_path / "Local_26.6_23G71_Restore.ipsw"
        local.write_bytes(b"fake")

        app = make_app(devices=sample_devices)
        app._restore_ipsw_path = local
        app.restore_ipsw_path_label.setText(str(local))

        captured: dict = {}

        def fake_restore(udid, ipsw_path, cache_dir, progress_callback, ecid=None):
            captured["progress_callback"] = progress_callback
            return SimpleNamespace(success=True, error=None, udid="x", ipsw_path=local)

        monkeypatch.setattr(gui_qt, "engine_restore_device", fake_restore)

        app._start_restore()
        captured["progress_callback"](ProgressEvent(text="Sending NAND image..."))

        assert "Sending NAND image..." in app.restore_log_text.toPlainText()

    def test_failed_restore_logs_error(self, make_app, sample_devices, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        app.restore_versions_combo.addItem(
            "iOS 26.6 (23G71)", userData="https://example.com/iPad_Pro_26.6_23G71_Restore.ipsw"
        )
        monkeypatch.setattr(
            gui_qt, "download_ipsw",
            lambda url, dest_dir, progress_callback=None: Path("/tmp/fake.ipsw"),
        )
        monkeypatch.setattr(
            gui_qt, "engine_restore_device",
            lambda **kwargs: SimpleNamespace(
                success=False, error="idevicerestore exited with code 1", udid="x"
            ),
        )

        app._start_restore()

        assert "idevicerestore exited with code 1" in app.restore_log_text.toPlainText()

    def test_download_phase_drives_progress_bar(
        self, make_app, sample_devices, tmp_path, monkeypatch
    ):
        """Download progress must move the bar before the restore starts.

        The GUI passes its ``on_progress`` callback into ``download_ipsw``, so
        percent events from the download phase switch the bar to determinate
        and move it. (The mocked restore reports failure so ``_finalize`` does
        not overwrite the download's value with 100.)
        """
        import apple_device_cli.gui_qt as gui_qt

        ipsw_path = tmp_path / "iPad_Pro_26.6_23G71_Restore.ipsw"
        ipsw_path.write_bytes(b"fake")

        app = make_app(devices=sample_devices)
        app.restore_versions_combo.addItem(
            "iOS 26.6 (23G71)",
            userData="https://example.com/iPad_Pro_26.6_23G71_Restore.ipsw",
        )

        def fake_download(url, dest_dir, progress_callback=None):
            progress_callback(
                ProgressEvent(
                    text="Downloaded 42%",
                    progress=ProgressUpdate(
                        kind="percent", value=42, total=1024, label=None
                    ),
                )
            )
            return ipsw_path

        monkeypatch.setattr(gui_qt, "download_ipsw", fake_download)
        monkeypatch.setattr(
            gui_qt,
            "engine_restore_device",
            lambda **kwargs: SimpleNamespace(
                success=False,
                error="stopped before restore",
                udid="x",
                ipsw_path=ipsw_path,
            ),
        )

        app._start_restore()

        assert app.restore_progress_bar.maximum() == 100
        assert app.restore_progress_bar.value() == 42

    def test_start_restore_with_recovery_device_uses_ecid(
        self, make_app, tmp_path, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        local = tmp_path / "Local_26.6_23G71_Restore.ipsw"
        local.write_bytes(b"fake")

        app = make_app(devices=[])
        app._restore_ipsw_path = local
        app.restore_ipsw_path_label.setText(str(local))

        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: True)
        monkeypatch.setattr(gui_qt, "_device_ecid", lambda: "abc")

        captured: dict = {}
        fake_result = SimpleNamespace(success=True, error=None, udid=None, ipsw_path=local)

        def fake_restore(udid, ipsw_path, cache_dir, progress_callback, ecid=None):
            captured["udid"] = udid
            captured["ecid"] = ecid
            return fake_result

        monkeypatch.setattr(gui_qt, "engine_restore_device", fake_restore)

        app._start_restore()

        assert captured["udid"] is None
        assert captured["ecid"] == "abc"
        assert "restoring via ECID abc" in app.restore_log_text.toPlainText()

    def test_start_restore_with_no_device_and_no_recovery_warns(
        self, make_app, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: False)

        with patch.object(gui_qt.QMessageBox, "warning") as mock_warn:
            app = make_app(devices=[])
            app._start_restore()
        mock_warn.assert_called_once()
        assert len(app._workers) == 0


class TestRestoreCacheUi:
    def test_show_cache_logs_state(self, make_app, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "cache_state", lambda cache_dir: {
            "path": "/tmp/cache",
            "size_bytes": 1048576,
            "ipsw_count": 1,
            "ipsw_files": ["iPad_26.6_23G71_Restore.ipsw"],
        })

        app = make_app()
        app._show_cache()

        log = app.restore_log_text.toPlainText()
        assert "/tmp/cache" in log
        assert "1,048,576 bytes" in log
        assert "iPad_26.6_23G71_Restore.ipsw" in log

    def test_pick_cache_folder_updates_label(self, make_app, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(
            gui_qt.QFileDialog, "getExistingDirectory",
            staticmethod(lambda *a, **kw: "/var/mnt/Disk2/cache"),
        )
        # This test mocks write_cache_config to isolate the label-update behavior.
        # Real persistence is covered by test_pick_cache_folder_persists_to_config_json.
        monkeypatch.setattr(
            gui_qt, "write_cache_config", lambda cache_dir: None
        )

        app = make_app()
        app._pick_cache_folder()

        assert app.restore_cache_path_label.text() == "/var/mnt/Disk2/cache"
        assert "Cache folder set to /var/mnt/Disk2/cache" in app.restore_log_text.toPlainText()

    def test_pick_cache_folder_persists_to_config_json(self, make_app, monkeypatch, tmp_path):
        """Regression: picker must actually write config.json, not just update the label."""
        import json
        from pathlib import Path

        import apple_device_cli.gui_qt as gui_qt
        from apple_device_cli.restore import cache as cache_mod

        # Redirect Path.home() so the write lands in tmp_path, not real ~/.config
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("XDG_CACHE_HOME", str(fake_home / ".cache"))
        monkeypatch.delenv("IOS_ENROLL_CACHE_DIR", raising=False)
        # Force Path.home() to re-resolve (it's cached in some pathlib versions)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # Use a writable tmp path — resolve_cache_dir() mkdirs the chosen
        # folder, and a hardcoded /var/mnt path fails on hosts where it's
        # not writable.
        chosen_folder = str(tmp_path / "iosfirmwares")
        monkeypatch.setattr(
            gui_qt.QFileDialog, "getExistingDirectory",
            staticmethod(lambda *a, **kw: chosen_folder),
        )

        app = make_app()
        # make_app no-ops write_cache_config; restore the real one so the
        # write actually happens — that's the whole point of this test.
        monkeypatch.setattr(gui_qt, "write_cache_config", cache_mod.write_cache_config)
        app._pick_cache_folder()

        config_file = fake_home / ".config" / "ios-enroll" / "config.json"
        assert config_file.exists(), f"config.json not written at {config_file}"
        data = json.loads(config_file.read_text())
        assert data["cache_dir"] == chosen_folder

        # Round-trip: resolve_cache_dir() must return the chosen folder
        assert cache_mod.resolve_cache_dir() == Path(chosen_folder)

    def test_browse_ipsw_selects_local_file(self, make_app, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(
            gui_qt.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **kw: ("/tmp/Manual_26.6_23G71_Restore.ipsw", "iOS IPSW (*.ipsw)")),
        )

        app = make_app()
        app._browse_ipsw()

        assert app.restore_ipsw_path_label.text() == "/tmp/Manual_26.6_23G71_Restore.ipsw"
        assert app.restore_start_btn.isEnabled()
        assert not app.restore_versions_combo.isEnabled()


class TestRestoreProgressBar:
    """The QProgressBar is driven by parsed idevicerestore progress events."""

    def test_progress_bar_exists_in_restore_tab(self, make_app):
        app = make_app()
        assert app.restore_progress_bar is not None
        assert app.restore_progress_bar.objectName() == "restore_progress_bar"
        # Always visible (anchored to the bottom of the tab) so the
        # layout doesn't reflow when a restore starts. Idle state shows
        # "Ready" at 0%; restore start switches to indeterminate
        # "Working..." via _reset_restore_progress_bar.
        assert not app.restore_progress_bar.isHidden()
        assert app.restore_progress_bar.maximum() == 100  # determinate
        assert app.restore_progress_bar.value() == 0
        assert app.restore_progress_bar.format() == "Ready"

    def test_plain_progress_events_drive_value_and_format(self, make_app):
        app = make_app()
        app._reset_restore_progress_bar()
        assert not app.restore_progress_bar.isHidden()
        assert app.restore_progress_bar.maximum() == 0  # indeterminate
        assert app.restore_progress_bar.format() == "Working..."

        app._on_restore_progress_event(
            ProgressEvent(
                text="STEP: Restoring Baseband",
                progress=ProgressUpdate(kind="step", value=None, total=None, label="Restoring Baseband"),
            )
        )
        # First progress event switches to determinate; format shows the step.
        # A bare step at 0% is bumped to a 1% floor so the bar shows activity
        # (see _on_restore_progress_event).
        assert app.restore_progress_bar.maximum() == 100
        assert app.restore_progress_bar.text() == "Restoring Baseband 1%"

        app._on_restore_progress_event(
            ProgressEvent(
                text="PROGRESS: 12/30",
                progress=ProgressUpdate(kind="percent", value=40, total=30, label=None),
            )
        )
        assert app.restore_progress_bar.value() == 40
        assert app.restore_progress_bar.text() == "Restoring Baseband 40%"

    def test_step_complete_format_at_100(self, make_app):
        app = make_app()
        app._reset_restore_progress_bar()
        app._on_restore_progress_event(
            ProgressEvent(
                text="STEP: Restoring Baseband",
                progress=ProgressUpdate(kind="step", value=None, total=None, label="Restoring Baseband"),
            )
        )
        app._on_restore_progress_event(
            ProgressEvent(
                text="PROGRESS: 30/30",
                progress=ProgressUpdate(kind="percent", value=100, total=30, label=None),
            )
        )
        assert app.restore_progress_bar.value() == 100
        assert app.restore_progress_bar.text() == "Step complete: Baseband"

    def test_final_state_on_success_and_failure(self, make_app):
        app = make_app()
        app._reset_restore_progress_bar()
        app._finalize_restore_progress_bar(success=True)
        assert app.restore_progress_bar.value() == 100
        assert app.restore_progress_bar.text() == "Restore complete"

        failed_app = make_app()
        failed_app._reset_restore_progress_bar()
        failed_app._finalize_restore_progress_bar(success=False)
        assert failed_app.restore_progress_bar.text() == "Restore failed — see log"

    def test_plain_log_lines_do_not_touch_the_bar(self, make_app):
        app = make_app()
        app._reset_restore_progress_bar()
        app._on_restore_progress_event(ProgressEvent(text="Sending LLB (185208 bytes)..."))

        assert app.restore_progress_bar.maximum() == 0  # still indeterminate
        assert "Sending LLB" in app.restore_log_text.toPlainText()


class TestRestoreRefreshDevices:
    def test_restore_tab_refresh_devices_button_refreshes_combo(
        self, make_app, sample_devices
    ):
        app = make_app(devices=[])
        assert app.restore_device_combo.count() == 0

        with patch("apple_device_cli.gui_qt.list_devices", return_value=sample_devices):
            app.restore_refresh_devices_btn.click()

        assert app.restore_device_combo.count() == 1
        assert app.restore_device_combo.currentData() == sample_devices[0].udid

    def test_refresh_devices_calls_populate_restore_combo(
        self, make_app, sample_devices, monkeypatch
    ):
        app = make_app(devices=[])
        called: list[str] = []
        monkeypatch.setattr(app, "_populate_restore_device_combo", lambda: called.append(1))

        with patch("apple_device_cli.gui_qt.list_devices", return_value=sample_devices):
            app._refresh_devices()

        assert called == [1]


class TestRestoreModeLabel:
    def test_mode_label_exists_and_shows_dash_without_device(self, make_app):
        app = make_app(devices=[])
        assert app.restore_device_mode_label is not None
        assert app.restore_device_mode_label.text() == "—"

    def test_mode_label_updates_after_device_refresh(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "detect_device_mode", lambda udid: "normal")
        app = make_app(devices=sample_devices)
        app._update_mode_labels()
        assert app.restore_device_mode_label.text() == "normal"

    def test_mode_label_dash_when_selection_cleared(self, make_app, sample_devices):
        app = make_app(devices=sample_devices)
        app.restore_device_combo.setCurrentIndex(-1)
        assert app.restore_device_mode_label.text() == "—"


class TestRecoveryButtons:
    def test_recovery_buttons_exist_and_disabled_without_device(self, make_app):
        app = make_app(devices=[])
        assert app.restore_enter_recovery_btn is not None
        assert app.restore_exit_recovery_btn is not None
        assert not app.restore_enter_recovery_btn.isEnabled()
        assert not app.restore_exit_recovery_btn.isEnabled()

    def test_recovery_buttons_enabled_with_device_and_disabled_on_deselect(
        self, make_app, sample_devices
    ):
        app = make_app(devices=sample_devices)
        assert app.restore_enter_recovery_btn.isEnabled()
        assert app.restore_exit_recovery_btn.isEnabled()

        app.restore_device_combo.setCurrentIndex(-1)
        assert not app.restore_enter_recovery_btn.isEnabled()
        assert not app.restore_exit_recovery_btn.isEnabled()

    def test_enter_recovery_requires_confirmation(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        monkeypatch.setattr(
            gui_qt.QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.No,
        )
        with patch("apple_device_cli.gui_qt.enter_recovery_mode") as mock_enter:
            app._enter_recovery()
        mock_enter.assert_not_called()
        assert len(app._workers) == 0

    def test_enter_recovery_confirmed_calls_engine(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        monkeypatch.setattr(
            gui_qt.QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        with patch("apple_device_cli.gui_qt.enter_recovery_mode") as mock_enter:
            app._enter_recovery()
        mock_enter.assert_called_once_with(sample_devices[0].udid)

    def test_exit_recovery_calls_engine(self, make_app, sample_devices, monkeypatch):
        app = make_app(devices=sample_devices)
        with patch("apple_device_cli.gui_qt.exit_recovery_mode") as mock_exit:
            app._exit_recovery()
        mock_exit.assert_called_once_with(sample_devices[0].udid)

    def test_exit_recovery_with_empty_combo_falls_back_to_any(
        self, make_app, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=[])
        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: True)
        with patch("apple_device_cli.gui_qt.exit_recovery_mode") as mock_exit:
            with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
                app._exit_recovery()
        mock_exit.assert_called_once_with(udid=None)

    def test_exit_recovery_with_selected_device_uses_udid(
        self, make_app, sample_devices, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: True)
        with patch("apple_device_cli.gui_qt.exit_recovery_mode") as mock_exit:
            app._exit_recovery()
        mock_exit.assert_called_once_with(sample_devices[0].udid)

    def test_exit_recovery_with_empty_combo_and_no_recovery_does_nothing(
        self, make_app, monkeypatch
    ):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=[])
        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: False)
        with patch("apple_device_cli.gui_qt.exit_recovery_mode") as mock_exit:
            app._exit_recovery()
        mock_exit.assert_not_called()
        assert len(app._workers) == 0

    def test_recovery_result_refreshes_device_list(
        self, make_app, sample_devices, monkeypatch
    ):
        app = make_app(devices=sample_devices)
        with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
            app._on_recovery_mode_result(None, None)
        assert app.restore_device_combo.count() == 0
        assert "Recovery mode operation completed" in app.restore_log_text.toPlainText()

    def test_recovery_result_error_is_logged(self, make_app, sample_devices):
        app = make_app(devices=sample_devices)
        app._on_recovery_mode_result(None, RuntimeError("enter recovery failed"))
        assert "enter recovery failed" in app.restore_log_text.toPlainText()


class TestExitRecoveryAnyButton:
    """The always-enabled 'Exit Recovery (any device)' button.

    A device in Recovery mode is invisible to usbmuxd, so it never appears in
    the Restore tab's device dropdown — the selection-based Exit Recovery
    button cannot reach it. This button scans the USB bus directly, no UDID.
    """

    def test_exit_recovery_button_without_selection_exists(self, make_app):
        app = make_app(devices=[])
        assert app.restore_exit_recovery_any_btn is not None
        # Enabled even with no device selected — the whole point of the button.
        assert app.restore_exit_recovery_any_btn.isEnabled()

    def test_exit_recovery_button_without_selection_invokes_engine_with_no_udid(
        self, make_app, monkeypatch
    ):
        app = make_app(devices=[])
        with patch("apple_device_cli.gui_qt.exit_recovery_mode") as mock_exit:
            with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
                app.restore_exit_recovery_any_btn.click()
        mock_exit.assert_called_once_with(udid=None)


class TestRecoveryRestoreFlow:
    """Driving a restore from the synthetic '(Recovery mode)' combo entry.

    A device in Recovery mode is invisible to usbmuxd, so it never lands in
    ``self._devices``. Selecting the synthetic entry (SRNM/ECID as userData)
    must still enable Start — targeted by ECID — and surface cached IPSW
    files instead of signed versions (which need lockdown).
    """

    def _select_recovery(self, app, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        monkeypatch.setattr(gui_qt, "detect_recovery_devices_present", lambda: True)
        monkeypatch.setattr(
            gui_qt,
            "recovery_device_descriptor",
            lambda: ("jxmwm7422v", "00094daa01d80032"),
        )
        app._populate_restore_device_combo()

    def test_recovery_combo_selection_enables_start_with_cached_ipsw(
        self, make_app, tmp_path, monkeypatch
    ):
        cached = tmp_path / "iPad_Pro_26.6_23G71_Restore.ipsw"
        cached.write_bytes(b"fake")

        app = make_app(devices=[])
        self._select_recovery(app, monkeypatch)

        assert app._restore_is_recovery is True
        assert app.restore_product_type_label.text() == "Recovery mode"
        assert app.restore_start_btn.isEnabled()
        assert app.restore_versions_combo.count() == 1
        assert app.restore_versions_combo.currentData() == str(cached)
        assert app._restore_ipsw_path == cached
        assert app.restore_ipsw_path_label.text() == str(cached)

    def test_recovery_combo_selection_without_cached_ipsw(
        self, make_app, monkeypatch
    ):
        app = make_app(devices=[])
        self._select_recovery(app, monkeypatch)

        assert app._restore_is_recovery is True
        assert not app.restore_start_btn.isEnabled()
        assert app._restore_ipsw_path is None
        assert "No cached IPSW" in app.restore_log_text.toPlainText()

    def test_recovery_start_routes_to_ecid(self, make_app, tmp_path, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        cached = tmp_path / "iPad_Pro_26.6_23G71_Restore.ipsw"
        cached.write_bytes(b"fake")

        app = make_app(devices=[])
        self._select_recovery(app, monkeypatch)
        monkeypatch.setattr(gui_qt, "_device_ecid", lambda: "00094daa01d80032")

        captured: dict = {}
        fake_result = SimpleNamespace(success=True, error=None, udid=None, ipsw_path=cached)

        def fake_restore(udid, ipsw_path, cache_dir, progress_callback, ecid=None):
            captured["udid"] = udid
            captured["ecid"] = ecid
            captured["ipsw_path"] = ipsw_path
            return fake_result

        monkeypatch.setattr(gui_qt, "engine_restore_device", fake_restore)

        app._start_restore()

        assert captured["udid"] is None
        assert captured["ecid"] == "00094daa01d80032"
        assert captured["ipsw_path"] == cached

    def test_recovery_mode_label_shows_recovery(self, make_app, monkeypatch):
        app = make_app(devices=[])
        self._select_recovery(app, monkeypatch)

        assert app.restore_device_mode_label.text() == "Recovery"


class TestCachedVersionMarker:
    def test_versions_list_marks_cached(self, make_app, sample_devices, tmp_path):

        # A cached IPSW whose basename matches a signed-version URL.
        cached = tmp_path / "iPad13,4_26.6_23G71_Restore.ipsw"
        cached.write_bytes(b"x")
        version = SimpleNamespace(
            display_label="iOS 26.6 (23G71)",
            url="https://cdn.example/iPad13,4_26.6_23G71_Restore.ipsw",
        )
        monkeypatch = None  # noqa: F841 — fixture unused; resolve_cache_dir is mocked by make_app
        app = make_app(devices=sample_devices)
        app._load_versions("iPad13,4")
        # Simulate the refresh callback with the cached file present.
        app._on_versions_refreshed([version], None)
        assert app.restore_versions_combo.count() == 1
        assert "(cached)" in app.restore_versions_combo.itemText(0)

    def test_versions_list_not_cached(self, make_app, sample_devices):
        app = make_app(devices=sample_devices)
        version = SimpleNamespace(
            display_label="iOS 26.5.2 (23F84)",
            url="https://cdn.example/iPad13,4_26.5.2_23F84_Restore.ipsw",
        )
        app._on_versions_refreshed([version], None)
        assert app.restore_versions_combo.count() == 1
        assert "(cached)" not in app.restore_versions_combo.itemText(0)


class TestVerifyIpswButton:
    def test_verify_button_disabled_without_ipsw(self, make_app, sample_devices):
        app = make_app(devices=sample_devices)
        assert not app.restore_verify_btn.isEnabled()

    def test_verify_button_enabled_after_browse(self, make_app, sample_devices, tmp_path):
        app = make_app(devices=sample_devices)
        f = tmp_path / "iPad13,4_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"fake")
        app._restore_ipsw_path = f
        app._update_restore_verify_enabled()
        assert app.restore_verify_btn.isEnabled()

    def test_verify_button_enabled_with_cached_url(self, make_app, sample_devices, tmp_path):
        cached = tmp_path / "iPad13,4_26.6_23G71_Restore.ipsw"
        cached.write_bytes(b"x")
        app = make_app(devices=sample_devices)
        version = SimpleNamespace(
            display_label="iOS 26.6 (23G71)",
            url="https://cdn.example/iPad13,4_26.6_23G71_Restore.ipsw",
        )
        app._on_versions_refreshed([version], None)
        assert app.restore_verify_btn.isEnabled()

    def test_verify_runs_on_demand(self, make_app, sample_devices, tmp_path, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        f = tmp_path / "iPad13,4_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"fake")
        app._restore_ipsw_path = f
        app._update_restore_verify_enabled()

        called: list[Path] = []
        expected = VerifyResult(
            path=f,
            local_sha1="a" * 40,
            local_sha256="b" * 64,
            local_size=4,
            expected=None,
            sha1_match=None,
            sha256_match=None,
            size_match=None,
        )

        def fake_verify(path, device=None, build=None, version=None):
            called.append(Path(path))
            return expected

        monkeypatch.setattr(gui_qt, "verify_ipsw", fake_verify)
        monkeypatch.setattr(gui_qt.QMessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(gui_qt.QMessageBox, "warning", lambda *a, **k: None)

        app._verify_ipsw()
        assert len(called) == 1
        assert called[0] == f

    def test_verify_refuses_uncached_url(self, make_app, sample_devices, tmp_path, monkeypatch):
        import apple_device_cli.gui_qt as gui_qt

        app = make_app(devices=sample_devices)
        version = SimpleNamespace(
            display_label="iOS 26.5.2 (23F84)",
            url="https://cdn.example/iPad13,4_26.5.2_23F84_Restore.ipsw",
        )
        app._on_versions_refreshed([version], None)
        assert not app.restore_verify_btn.isEnabled()

        called: list = []
        monkeypatch.setattr(gui_qt, "verify_ipsw", lambda **kw: called.append(kw))
        monkeypatch.setattr(gui_qt.QMessageBox, "warning", lambda *a, **k: None)
        app._verify_ipsw()
        assert called == []
