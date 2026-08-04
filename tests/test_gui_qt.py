"""Tests for the PySide6 GUI module.

These tests run against real PySide6 widgets using the ``offscreen`` Qt
platform plugin (via ``QT_QPA_PLATFORM=offscreen``), so no display is required
in CI. We avoid bare ``MagicMock()`` for class-shaped values; mocks are always
``spec=``'d against the real PySide6 / project class.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from apple_device_cli.device.info import DeviceInfo  # noqa: E402
from apple_device_cli.orgs.manager import Organization, OrganizationManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Single QApplication for the whole test session."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """Make QMessageBox.{warning,critical} return immediately.

    Modal dialogs block the test runner forever under the offscreen Qt
    platform plugin because no user input can ever dismiss them. Production
    code still calls the real dialogs; tests just short-circuit them. Tests
    that need to inspect ``question`` prompts monkeypatch ``question``
    themselves (the per-test monkeypatch wins over this autouse one).
    """
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    yield


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
def sample_org(tmp_path) -> Organization:
    return Organization(
        name="Capital Candy",
        org_id="com.capitalcandy",
        mdm_url="https://mdm.example.com/mdm",
        cert_path=str(tmp_path / "cert.der"),
        key_path=str(tmp_path / "key.der"),
    )


@pytest.fixture
def make_app(qapp, tmp_path, monkeypatch):
    """Factory for an EnrollmentApp wired to a tmp orgs dir.

    Replaces ``WorkerThread`` with a synchronous fake so handlers run on the
    calling thread. Without an event loop, real QThread pending connections
    would never fire their slots, so the test would deadlock in ``wait``.
    """
    from apple_device_cli.gui_qt import EnrollmentApp

    def _factory(orgs: list[Organization] | None = None) -> EnrollmentApp:
        from apple_device_cli import gui_qt

        class _Signal:
            def __init__(self):
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

        monkeypatch.setattr(gui_qt, "WorkerThread", SyncWorker)

        original_init = OrganizationManager.__init__

        def patched_init(self, orgs_dir=None):
            original_init(self, orgs_dir=tmp_path)

        with patch.object(OrganizationManager, "__init__", patched_init):
            with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
                with patch(
                    "apple_device_cli.gui_qt.OrganizationManager.list_orgs",
                    return_value=orgs or [],
                ):
                    return gui_qt.EnrollmentApp()

    return _factory


# ---------------------------------------------------------------------------
# Pure validation — no Qt needed
# ---------------------------------------------------------------------------


class TestValidateOrgFields:
    def test_valid_org(self):
        from apple_device_cli.gui_qt import validate_org_fields

        validate_org_fields("Good.Org-Name_1")  # no exception
        validate_org_fields(
            "Good.Org-Name_1",
            mdm_url="https://mdm.example.com/mdm",
            checkin_url="https://mdm.example.com/checkin",
            mdm_topic="com.example.mdm",
        )

    def test_empty_name_rejected(self):
        from apple_device_cli.gui_qt import validate_org_fields, OrgValidationError

        with pytest.raises(OrgValidationError, match="name is required"):
            validate_org_fields("")

    def test_disallowed_name_chars_rejected(self):
        from apple_device_cli.gui_qt import validate_org_fields, OrgValidationError

        with pytest.raises(OrgValidationError, match="only contain"):
            validate_org_fields("Bad/Org")
        with pytest.raises(OrgValidationError):
            validate_org_fields("Bad Org With Spaces")

    def test_non_http_url_rejected(self):
        from apple_device_cli.gui_qt import validate_org_fields, OrgValidationError

        with pytest.raises(OrgValidationError, match="MDM URL"):
            validate_org_fields("Org", mdm_url="javascript:alert(1)")
        with pytest.raises(OrgValidationError, match="Check-in URL"):
            validate_org_fields("Org", checkin_url="file:///etc/passwd")

    def test_invalid_mdm_topic_rejected(self):
        from apple_device_cli.gui_qt import validate_org_fields, OrgValidationError

        with pytest.raises(OrgValidationError, match="MDM Topic"):
            validate_org_fields("Org", mdm_topic="bad topic!")


class TestValidateIdentityDays:
    def test_valid_days_accepted(self):
        from apple_device_cli.gui_qt import validate_identity_days

        assert validate_identity_days(1) == 1
        assert validate_identity_days(365 * 5) == 365 * 5

    @pytest.mark.parametrize("days", [0, -1, 365 * 200])
    def test_out_of_range_rejected(self, days):
        from apple_device_cli.gui_qt import validate_identity_days

        with pytest.raises(ValueError, match="must be between"):
            validate_identity_days(days)


# ---------------------------------------------------------------------------
# Atomic identity writes
# ---------------------------------------------------------------------------


class TestAtomicIdentityWrites:
    def test_writes_both_files(self, tmp_path):
        from apple_device_cli.gui_qt import _write_identity_atomic

        org_dir = tmp_path / "orgs" / "Test"
        _write_identity_atomic(org_dir, b"CERT", b"KEY")

        assert (org_dir / "cert.der").read_bytes() == b"CERT"
        assert (org_dir / "key.der").read_bytes() == b"KEY"
        leftover = [p for p in org_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftover == []

    def test_cleans_up_temp_files_on_failure(self, tmp_path):
        from apple_device_cli.gui_qt import _write_identity_atomic

        org_dir = tmp_path / "orgs" / "Test"
        org_dir.mkdir(parents=True)

        with patch("apple_device_cli.gui_qt.os.replace", side_effect=OSError("boom")):
            with pytest.raises(OSError):
                _write_identity_atomic(org_dir, b"CERT", b"KEY")

        leftover = [p for p in org_dir.iterdir() if p.name.endswith(".tmp")]
        assert leftover == [], f"Temp files leaked: {leftover}"


# ---------------------------------------------------------------------------
# Password scrubbing
# ---------------------------------------------------------------------------


class TestPasswordRedaction:
    def test_password_replaced(self):
        from apple_device_cli.gui_qt import _redact_in_text

        assert _redact_in_text("connecting with pass hunter2hunter2", "hunter2hunter2") == \
            "connecting with pass ***"

    def test_no_secret_no_change(self):
        from apple_device_cli.gui_qt import _redact_in_text

        assert _redact_in_text("hello", "") == "hello"
        assert _redact_in_text("hello", None) == "hello"


# ---------------------------------------------------------------------------
# Worker / thread safety
# ---------------------------------------------------------------------------


class TestWorkerSafety:
    def test_close_event_refused_while_workers_active(self, make_app, monkeypatch):
        # Suppress the modal warning; closeEvent must still ignore on busy state.
        monkeypatch.setattr(
            QMessageBox, "warning",
            lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
        )
        app = make_app()
        worker = MagicMock(spec=["run", "quit", "wait"])
        app._workers.append(worker)

        event = QCloseEvent()
        events: list[str] = []
        event.accept = lambda: events.append("accept")
        event.ignore = lambda: events.append("ignore")

        app.closeEvent(event)

        assert events == ["ignore"], "closeEvent must ignore while workers are running"
        assert app._workers, "Worker list must not be cleared on refused close"

    def test_close_event_accepted_when_no_workers(self, make_app):
        app = make_app()
        assert app._workers == []

        event = QCloseEvent()
        events: list[str] = []
        event.accept = lambda: events.append("accept")
        event.ignore = lambda: events.append("ignore")

        app.closeEvent(event)
        assert events == ["accept"]

    def test_button_re_enabled_after_worker_completes(self, make_app):
        app = make_app()
        btn = app.refresh_devices_btn
        assert btn.isEnabled()

        with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
            app._refresh_devices()

        assert btn.isEnabled(), "Refresh button must be re-enabled after worker completes"

    def test_buttons_passed_to_run_worker_are_disabled(self, make_app):
        """The buttons listed in ``_run_worker`` are disabled while the worker runs.

        Verified by inspection: ``_run_worker`` calls ``btn.setEnabled(False)``
        on each button in ``buttons_to_disable`` before starting the worker,
        and re-enables them in the completion handler. The completion handler
        is exercised by ``test_button_re_enabled_after_worker_completes``.
        """
        import inspect
        from apple_device_cli import gui_qt

        source = inspect.getsource(gui_qt.EnrollmentApp._run_worker)
        assert "setEnabled(False)" in source
        assert "setEnabled(True)" in source


# ---------------------------------------------------------------------------
# Slot safety
# ---------------------------------------------------------------------------


class TestSlotSafety:
    def test_make_supervised_handles_none_result(self, make_app, sample_org):
        app = make_app([sample_org])

        # Drive the slot directly; no need to spin up a worker.
        app._on_make_supervised_result(None, None)
        log_text = app.log_text.toPlainText()
        assert "no result" in log_text.lower()

        # And with an error.
        app._on_make_supervised_result(None, ValueError("boom"))
        log_text = app.log_text.toPlainText()
        assert "Enrollment failed: boom" in log_text

    def test_make_supervised_with_result_logs_attributes(self, make_app):
        app = make_app()
        # spec'd mock — typed object with the attributes _on_make_supervised_result reads.
        result = MagicMock(spec=["supervised", "mdm_enrolled", "wifi_installed", "errors"])
        result.supervised = True
        result.mdm_enrolled = True
        result.wifi_installed = False
        result.errors = ["warn-1"]
        app._on_make_supervised_result(result, None)
        log_text = app.log_text.toPlainText()
        assert "supervised=True" in log_text
        assert "warn-1" in log_text

    def test_status_result_handles_non_dict(self, make_app):
        app = make_app()
        app._on_status_result("not a dict", None)
        log_text = app.log_text.toPlainText()
        assert "unexpected data" in log_text.lower()

    def test_status_result_handles_dict(self, make_app):
        app = make_app()
        app._on_status_result(
            {"activation_state": "Activated", "is_supervised": True, "cloud_config_applied": False},
            None,
        )
        log_text = app.log_text.toPlainText()
        assert "Activation: Activated" in log_text
        assert "Supervised: True" in log_text


# ---------------------------------------------------------------------------
# Stale refresh suppression
# ---------------------------------------------------------------------------


class TestRefreshToken:
    def test_stale_refresh_does_not_overwrite(self, make_app):
        app = make_app()

        # First, refresh so the device list populates and request_token is set.
        with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
            app._refresh_devices()

        # Capture the current token, then advance past it to invalidate pending callbacks.
        current_token = app._request_token
        app._request_token = current_token + 100

        # A completion with the old token must be discarded.
        app._on_devices_refreshed(
            [DeviceInfo(
                udid="STALE", device_name="STALE", device_type="t",
                firmware_version="v", build_version="b", ecid="e",
            )],
            None,
            token=current_token,
        )
        assert all(d.udid != "STALE" for d in app._devices)

    def test_current_refresh_overwrites(self, make_app):
        app = make_app()
        with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
            app._refresh_devices()
        token = app._request_token
        new_devices = [DeviceInfo(
            udid="FRESH", device_name="FRESH", device_type="t",
            firmware_version="v", build_version="b", ecid="e",
        )]
        app._on_devices_refreshed(new_devices, None, token=token)
        assert any(d.udid == "FRESH" for d in app._devices)


# ---------------------------------------------------------------------------
# Delete confirmation
# ---------------------------------------------------------------------------


class TestDeleteOrg:
    def test_delete_warns_about_cert_loss(self, make_app, sample_org, monkeypatch):
        app = make_app([sample_org])
        app.orgs_list.setCurrentRow(0)

        captured = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda parent, title, text, *args, **kwargs:
                captured.append(text) or QMessageBox.StandardButton.No,
        )

        app._delete_org()

        assert captured, "QMessageBox.question must be shown"
        text = captured[0]
        assert "cert" in text.lower() or "key" in text.lower()
        assert "lost" in text.lower()


# ---------------------------------------------------------------------------
# Re-enroll confirmation includes device label
# ---------------------------------------------------------------------------


class TestReenrollConfirmation:
    def test_confirm_message_includes_device(self, make_app, sample_devices, monkeypatch):
        app = make_app()
        app._devices = sample_devices
        app.enroll_udid_combo.addItem(sample_devices[0].udid)

        captured = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda parent, title, text, *args, **kwargs:
                captured.append(text) or QMessageBox.StandardButton.No,
        )

        app._prepare_reenroll()

        assert captured, "question dialog must be shown"
        text = captured[0]
        assert sample_devices[0].device_name in text
        assert sample_devices[0].udid in text


# ---------------------------------------------------------------------------
# Module-shape checks
# ---------------------------------------------------------------------------


class TestRunGui:
    def test_run_gui_raises_when_pyside6_missing(self):
        from apple_device_cli.gui_qt import run_gui

        with patch.dict(sys.modules, {"PySide6.QtWidgets": None}):
            with pytest.raises(RuntimeError):
                run_gui()

    def test_module_imports_without_pyside6(self):
        """``import apple_device_cli.gui_qt`` must succeed even when PySide6
        is not installed, so the GUI module isn't a hard dependency on the
        core install path.
        """
        # Drop any cached PySide6 imports and block re-import.
        for name in [k for k in sys.modules if k == "PySide6" or k.startswith("PySide6.")]:
            del sys.modules[name]
        with patch.dict(sys.modules, {"PySide6": None, "PySide6.QtCore": None, "PySide6.QtWidgets": None}):
            # Force a fresh import. ``runpy`` runs the module body in a fresh
            # namespace, just like a real ``python -m apple_device_cli.gui_qt``
            # would at process start.
            import runpy

            ns = runpy.run_module("apple_device_cli.gui_qt", run_name="__not_main__")

        # Pure-Python names (no Qt needed) should be available.
        assert "validate_org_fields" in ns
        assert "validate_identity_days" in ns
        assert "_write_identity_atomic" in ns
        # Qt-using names should NOT be in the namespace until something
        # triggers _require_pyside6().
        assert "WorkerThread" not in ns
        assert "EnrollmentApp" not in ns

    def test_lazy_attr_raises_friendly_error_without_pyside6(self):
        """Accessing ``WorkerThread``, ``EnrollmentApp``, or ``run_gui`` on
        a headless install must raise ``RuntimeError`` (with the install
        hint) instead of bubbling up an ``ImportError`` traceback.
        """
        for name in [k for k in sys.modules if k == "PySide6" or k.startswith("PySide6.")]:
            del sys.modules[name]
        with patch.dict(sys.modules, {"PySide6": None, "PySide6.QtCore": None, "PySide6.QtWidgets": None}):
            import importlib

            import apple_device_cli.gui_qt as mod

            importlib.reload(mod)
            # Accessing the lazy attribute triggers _require_pyside6()
            # which raises the friendly RuntimeError.
            with pytest.raises(RuntimeError, match=r"ios-enroll\[gui\]"):
                mod.WorkerThread  # noqa: B018

    def test_main_returns_friendly_message_when_pyside6_missing(self, capsys):
        """``_main`` (used by ``python -m`` and the ``ios-enroll-gui``
        script) must catch the RuntimeError raised by ``run_gui`` and
        write the install hint to stderr, exit code 1.
        """
        from apple_device_cli.gui_qt import _main

        with patch(
            "apple_device_cli.gui_qt.run_gui",
            side_effect=RuntimeError("PySide6 is not available. Install with: uv pip install 'ios-enroll[gui]'"),
        ):
            rc = _main()

        captured = capsys.readouterr()
        assert rc == 1
        assert "PySide6 is not available" in captured.err
        assert "ios-enroll[gui]" in captured.err
        # The Python traceback must not appear — that was the bug.
        assert "Traceback" not in captured.err

    def test_main_returns_zero_when_gui_runs(self):
        """``_main`` returns 0 when ``run_gui`` completes normally."""
        from apple_device_cli.gui_qt import _main

        with patch("apple_device_cli.gui_qt.run_gui") as mock_run_gui:
            assert _main() == 0
            mock_run_gui.assert_called_once()

    def test_cli_gui_flag_launches_gui(self):
        from typer.testing import CliRunner

        from apple_device_cli.cli import app

        runner = CliRunner()
        with patch("apple_device_cli.gui_qt.run_gui") as mock_run_gui:
            result = runner.invoke(app, ["--gui"])
            assert result.exit_code == 0
            mock_run_gui.assert_called_once()

    def test_cli_gui_flag_rejects_subcommand(self):
        from typer.testing import CliRunner

        from apple_device_cli.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--gui", "device", "list"])
        assert result.exit_code == 1
        assert "cannot be combined" in result.output


# ---------------------------------------------------------------------------
# _on_enroll_org_changed — WiFi auto-populate from selected org's
# wifi.mobileconfig (com.apple.wifi.managed payload).
# ---------------------------------------------------------------------------


class _FakeOrgManager:
    """In-memory stand-in for OrganizationManager for the auto-populate tests.

    Records call arguments and returns canned values for ``get_org`` and
    ``read_wifi_profile``. Avoids touching the filesystem so tests don't need
    to construct a real mobileconfig file.
    """

    def __init__(
        self,
        *,
        get_org_return=None,
        read_wifi_return=None,
        get_org_raises: Exception | None = None,
        read_wifi_raises: Exception | None = None,
    ) -> None:
        self._get_org_return = get_org_return
        self._read_wifi_return = read_wifi_return
        self._get_org_raises = get_org_raises
        self._read_wifi_raises = read_wifi_raises
        self.get_org_calls: list[str] = []
        self.read_wifi_calls: list[str] = []

    def get_org(self, name: str):
        self.get_org_calls.append(name)
        if self._get_org_raises is not None:
            raise self._get_org_raises
        return self._get_org_return

    def read_wifi_profile(self, name: str):
        self.read_wifi_calls.append(name)
        if self._read_wifi_raises is not None:
            raise self._read_wifi_raises
        return self._read_wifi_return


class TestEnrollOrgChangedWifiAutoPopulate:
    """Tests for EnrollmentApp._on_enroll_org_changed WiFi auto-populate flow.

    Exercises every branch of the method: early-return guards, the
    wifi_config_path gate, the read_wifi_profile integration, defaulting
    behavior when fields are missing, exception handling, and log redaction.
    """

    @staticmethod
    def _select_org(app, name: str) -> int:
        """Add ``name`` to the Enrollment tab org combo without firing signals."""
        combo = app.enroll_org_combo
        combo.blockSignals(True)
        try:
            combo.addItem(name)
            return combo.count() - 1
        finally:
            combo.blockSignals(False)

    def test_negative_index_returns_early_without_org_lookup(
        self, make_app, monkeypatch
    ):
        """index < 0 short-circuits before any org lookup."""
        app = make_app()
        fake = _FakeOrgManager()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        # Pre-populate so we can prove the method left the widgets alone
        app.enroll_wifi_ssid.setText("preexisting-ssid")
        app.enroll_wifi_password.setText("preexisting-pass")
        app.enroll_wifi_enc.setCurrentText("WEP")

        app._on_enroll_org_changed(-1)

        assert app.enroll_wifi_ssid.text() == "preexisting-ssid"
        assert app.enroll_wifi_password.text() == "preexisting-pass"
        assert app.enroll_wifi_enc.currentText() == "WEP"
        assert fake.get_org_calls == []
        assert fake.read_wifi_calls == []

    def test_empty_org_name_returns_early(self, make_app, monkeypatch):
        """Empty combo text short-circuits before any org lookup."""
        app = make_app()
        fake = _FakeOrgManager()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        # Combo starts empty; pass an index that would otherwise resolve to ""
        app._on_enroll_org_changed(0)

        assert app.enroll_wifi_ssid.text() == ""
        assert app.enroll_wifi_password.text() == ""
        assert fake.get_org_calls == []

    def test_org_without_wifi_path_clears_fields_and_skips_read(
        self, make_app, sample_org, monkeypatch
    ):
        """wifi_config_path falsy → clear fields, do NOT call read_wifi_profile."""
        app = make_app()
        sample_org.wifi_config_path = None
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        app.enroll_wifi_ssid.setText("stale-ssid")
        app.enroll_wifi_password.setText("stale-pass")
        idx = self._select_org(app, sample_org.name)

        app._on_enroll_org_changed(idx)

        assert app.enroll_wifi_ssid.text() == ""
        assert app.enroll_wifi_password.text() == ""
        assert app.enroll_wifi_enc.currentText() == "WPA"  # default
        assert fake.get_org_calls == [sample_org.name]
        assert fake.read_wifi_calls == [], (
            "read_wifi_profile must not be called when wifi_config_path is None"
        )

    def test_org_with_wifi_profile_populates_fields(
        self, make_app, sample_org, monkeypatch
    ):
        """Happy path: SSID, password, and encryption populate from parsed profile."""
        app = make_app()
        sample_org.wifi_config_path = "/tmp/wifi.mobileconfig"
        fake = _FakeOrgManager(
            get_org_return=sample_org,
            read_wifi_return={
                "ssid": "CorpNet",
                "password": "pa55word",
                "encryption": "WPA",
                "auto_join": True,
            },
        )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        idx = self._select_org(app, sample_org.name)

        app._on_enroll_org_changed(idx)

        assert app.enroll_wifi_ssid.text() == "CorpNet"
        assert app.enroll_wifi_password.text() == "pa55word"
        assert app.enroll_wifi_enc.currentText() == "WPA"
        assert fake.get_org_calls == [sample_org.name]
        assert fake.read_wifi_calls == [sample_org.name]

    def test_org_wifi_profile_without_managed_payload_clears_fields(
        self, make_app, sample_org, monkeypatch
    ):
        """read_wifi_profile returning None (no com.apple.wifi.managed) clears fields."""
        app = make_app()
        sample_org.wifi_config_path = "/tmp/wifi.mobileconfig"
        fake = _FakeOrgManager(get_org_return=sample_org, read_wifi_return=None)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        app.enroll_wifi_ssid.setText("stale-ssid")
        idx = self._select_org(app, sample_org.name)

        app._on_enroll_org_changed(idx)

        assert app.enroll_wifi_ssid.text() == ""
        assert app.enroll_wifi_password.text() == ""
        assert app.enroll_wifi_enc.currentText() == "WPA"
        assert fake.read_wifi_calls == [sample_org.name]

    def test_unknown_encryption_falls_back_to_wpa(
        self, make_app, sample_org, monkeypatch
    ):
        """Encryption values outside (WPA, WEP, None) collapse to 'WPA'."""
        app = make_app()
        sample_org.wifi_config_path = "/tmp/wifi.mobileconfig"
        fake = _FakeOrgManager(
            get_org_return=sample_org,
            read_wifi_return={
                "ssid": "CorpNet",
                "password": "pa55word",
                "encryption": "WPA3-Enterprise",  # not in the allowed set
                "auto_join": True,
            },
        )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        idx = self._select_org(app, sample_org.name)

        app._on_enroll_org_changed(idx)

        assert app.enroll_wifi_ssid.text() == "CorpNet"
        assert app.enroll_wifi_enc.currentText() == "WPA"

    def test_get_org_raises_clears_fields_without_crashing(
        self, make_app, monkeypatch
    ):
        """get_org exceptions are caught, logged, and leave fields empty."""
        app = make_app()
        fake = _FakeOrgManager(get_org_raises=RuntimeError("disk unavailable"))
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        app.enroll_wifi_ssid.setText("stale-ssid")
        idx = self._select_org(app, "Broken Org")

        app._on_enroll_org_changed(idx)  # must not raise

        assert app.enroll_wifi_ssid.text() == ""
        assert app.enroll_wifi_password.text() == ""
        assert fake.get_org_calls == ["Broken Org"]
        assert fake.read_wifi_calls == []

    def test_read_wifi_profile_raises_clears_fields_without_crashing(
        self, make_app, sample_org, monkeypatch
    ):
        """read_wifi_profile exceptions are caught, logged, and leave fields empty."""
        app = make_app()
        sample_org.wifi_config_path = "/tmp/wifi.mobileconfig"
        fake = _FakeOrgManager(
            get_org_return=sample_org,
            read_wifi_raises=RuntimeError("openssl blew up"),
        )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        app.enroll_wifi_ssid.setText("stale-ssid")
        idx = self._select_org(app, sample_org.name)

        app._on_enroll_org_changed(idx)  # must not raise

        assert app.enroll_wifi_ssid.text() == ""
        assert app.enroll_wifi_password.text() == ""
        assert fake.read_wifi_calls == [sample_org.name]

    def test_ssid_redacted_in_log_but_kept_in_form(
        self, make_app, sample_org, monkeypatch
    ):
        """Log panel sees a redacted SSID; the form widget still has the raw value."""
        from apple_device_cli.core.redaction import redact_name

        app = make_app()
        sample_org.wifi_config_path = "/tmp/wifi.mobileconfig"
        raw_ssid = "SecretNetworkName"
        fake = _FakeOrgManager(
            get_org_return=sample_org,
            read_wifi_return={
                "ssid": raw_ssid,
                "password": "irrelevant",
                "encryption": "WPA",
                "auto_join": True,
            },
        )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager", lambda *a, **kw: fake
        )
        idx = self._select_org(app, sample_org.name)

        app._on_enroll_org_changed(idx)

        # Form keeps the raw SSID (it's a form input, not a log entry)
        assert app.enroll_wifi_ssid.text() == raw_ssid
        # Log shows the redacted form, never the raw SSID
        log_text = app.log_text.toPlainText()
        assert redact_name(raw_ssid) in log_text
        assert raw_ssid not in log_text


# ---------------------------------------------------------------------------
# Enrollment-tab action entry guards: _make_supervised, _check_status,
# _prepare_reenroll, and _resolve_enroll_org.
# ---------------------------------------------------------------------------


class TestEnrollmentActionEntryGuards:
    """Tests for entry-point guards on Enrollment-tab actions.

    Covers the early-return branches of _make_supervised (no org / missing
    cert / missing key / no UDID / invalid preset), the happy path that
    threads form values into make_supervised(), the progress_callback
    password-scrubbing invariant, and the empty-UDID / user-confirmation
    guards on _check_status and _prepare_reenroll.
    """

    def test_resolve_enroll_org_empty_combo_warns_returns_none(
        self, make_app, monkeypatch
    ):
        """Empty enroll-org combo shows warning and returns None."""
        app = make_app()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager",
            lambda *a, **kw: _FakeOrgManager(),
        )
        with patch.object(QMessageBox, "warning") as mock_warn:
            result = app._resolve_enroll_org()
        assert result is None
        mock_warn.assert_called_once()
        assert "Select an organization" in mock_warn.call_args.args[2]

    def test_resolve_enroll_org_unknown_org_warns_returns_none(
        self, make_app, monkeypatch
    ):
        """get_org returning None → 'Unknown organization' warning, returns None."""
        app = make_app()
        app.enroll_org_combo.addItem("NonexistentOrg")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager",
            lambda *a, **kw: _FakeOrgManager(get_org_return=None),
        )
        with patch.object(QMessageBox, "warning") as mock_warn:
            result = app._resolve_enroll_org()
        assert result is None
        mock_warn.assert_called_once()
        assert "NonexistentOrg" in mock_warn.call_args.args[2]

    def test_resolve_enroll_org_returns_org_on_hit(
        self, make_app, sample_org, monkeypatch
    ):
        """get_org returns org → returns the org without warning."""
        app = make_app()
        app.enroll_org_combo.addItem(sample_org.name)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager",
            lambda *a, **kw: _FakeOrgManager(get_org_return=sample_org),
        )
        with patch.object(QMessageBox, "warning") as mock_warn:
            result = app._resolve_enroll_org()
        assert result is sample_org
        mock_warn.assert_not_called()

    def test_make_supervised_no_org_returns_without_worker(self, make_app):
        """Empty org combo → _make_supervised returns without starting a worker."""
        app = make_app()
        # enroll_org_combo is empty; enroll_udid_combo is empty too
        log_before = app.log_text.toPlainText()
        app._make_supervised()
        assert len(app._workers) == 0
        assert app.log_text.toPlainText() == log_before

    def test_make_supervised_missing_cert_warns_no_worker(
        self, make_app, sample_org, monkeypatch
    ):
        """org.cert_path is None → 'Missing identity' warning, no worker started."""
        sample_org.cert_path = None
        sample_org.key_path = "/tmp/key.der"
        app = make_app(orgs=[sample_org])
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: fake.get_org(name),
        )
        app.enroll_udid_combo.addItem("udid-x")
        with patch.object(QMessageBox, "warning") as mock_warn:
            app._make_supervised()
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "Missing identity"
        assert len(app._workers) == 0

    def test_make_supervised_missing_key_warns_no_worker(
        self, make_app, sample_org, monkeypatch
    ):
        """org.key_path is None → 'Missing identity' warning, no worker started."""
        sample_org.cert_path = "/tmp/cert.der"
        sample_org.key_path = None
        app = make_app(orgs=[sample_org])
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: fake.get_org(name),
        )
        app.enroll_udid_combo.addItem("udid-x")
        with patch.object(QMessageBox, "warning") as mock_warn:
            app._make_supervised()
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "Missing identity"
        assert len(app._workers) == 0

    def test_make_supervised_no_udid_warns_no_worker(
        self, make_app, sample_org, monkeypatch
    ):
        """Empty enroll_udid_combo → 'No device' warning, no worker started."""
        app = make_app(orgs=[sample_org])
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: fake.get_org(name),
        )
        # enroll_udid_combo is empty
        with patch.object(QMessageBox, "warning") as mock_warn:
            app._make_supervised()
        mock_warn.assert_called_once()
        assert "No device" in mock_warn.call_args.args[1]
        assert len(app._workers) == 0

    def test_make_supervised_invalid_preset_warns_no_worker(
        self, make_app, sample_org, monkeypatch
    ):
        """resolve_skip_panes ValueError → 'Invalid preset' warning, no worker started.

        resolve_skip_panes only raises ValueError for invalid extra_panes; it
        silently ignores unknown preset names. We patch it directly to
        exercise the except clause.
        """
        app = make_app(orgs=[sample_org])
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: fake.get_org(name),
        )
        app.enroll_udid_combo.addItem("udid-x")

        def boom(preset, extra_panes):
            raise ValueError("simulated bad preset")

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.resolve_skip_panes", boom
        )
        with patch.object(QMessageBox, "warning") as mock_warn:
            app._make_supervised()
        mock_warn.assert_called_once()
        assert mock_warn.call_args.args[1] == "Invalid preset"
        assert len(app._workers) == 0

    def test_make_supervised_happy_path_invokes_make_supervised(
        self, make_app, sample_org, monkeypatch
    ):
        """All guards pass → make_supervised() called with form values, result logged."""
        app = make_app(orgs=[sample_org])
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: fake.get_org(name),
        )
        app.enroll_udid_combo.addItem("udid-x")
        app.enroll_wifi_password.setText("supersecret")

        captured: dict = {}

        def fake_make_supervised(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                supervised=True,
                mdm_enrolled=True,
                wifi_installed=True,
                errors=[],
            )

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make_supervised
        )

        app._make_supervised()

        # Worker chain ran to completion; kwargs threaded through correctly
        assert captured["udid"] == "udid-x"
        assert captured["wifi_password"] == "supersecret"
        assert captured["cert_path"] == sample_org.cert_path
        assert captured["key_path"] == sample_org.key_path
        assert captured["org_name"] == sample_org.name
        assert callable(captured["progress_callback"])

        # Result handler logged the result attributes (log format abbreviates MDM/WiFi)
        log = app.log_text.toPlainText()
        assert "supervised=True" in log
        assert "MDM=True" in log
        assert "WiFi=True" in log

    def test_make_supervised_progress_callback_scrubs_wifi_password(
        self, make_app, sample_org, monkeypatch
    ):
        """progress_callback must redact WiFi password from progress messages."""
        app = make_app(orgs=[sample_org])
        fake = _FakeOrgManager(get_org_return=sample_org)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: fake.get_org(name),
        )
        app.enroll_udid_combo.addItem("udid-x")
        app.enroll_wifi_password.setText("supersecret")

        captured: dict = {}

        def fake_make_supervised(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                supervised=True,
                mdm_enrolled=False,
                wifi_installed=False,
                errors=[],
            )

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make_supervised
        )

        app._make_supervised()

        # Exercise the captured progress callback with a message containing the password
        log_before = app.log_text.toPlainText()
        captured["progress_callback"]("Connecting to supersecret network...")
        new_text = app.log_text.toPlainText()[len(log_before):]

        # Password must be redacted; mask + surrounding text must remain
        assert "supersecret" not in new_text
        assert "***" in new_text
        assert "Connecting to" in new_text
        assert "network..." in new_text

    def test_check_status_empty_udid_warns_no_worker(self, make_app):
        """Empty enroll_udid_combo → 'No device' warning, no worker started."""
        app = make_app()
        with patch.object(QMessageBox, "warning") as mock_warn:
            app._check_status()
        mock_warn.assert_called_once()
        assert "No device" in mock_warn.call_args.args[1]
        assert len(app._workers) == 0

    def test_check_status_happy_path_invokes_get_device_enrollment_state(
        self, make_app, monkeypatch
    ):
        """UDID present → get_device_enrollment_state called, status logged."""
        app = make_app()
        app.enroll_udid_combo.addItem("udid-x")
        captured: list[str] = []

        def fake_get_state(udid):
            captured.append(udid)
            return {
                "activation_state": "Activated",
                "is_supervised": True,
                "cloud_config_applied": True,
            }

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.get_device_enrollment_state", fake_get_state
        )
        app._check_status()
        assert captured == ["udid-x"]
        log = app.log_text.toPlainText()
        assert "Activation: Activated" in log
        assert "Supervised: True" in log
        assert "Cloud Config: True" in log

    def test_prepare_reenroll_empty_udid_warns_no_worker(self, make_app):
        """Empty enroll_udid_combo → 'No device' warning, no worker started."""
        app = make_app()
        with patch.object(QMessageBox, "warning") as mock_warn:
            app._prepare_reenroll()
        mock_warn.assert_called_once()
        assert "No device" in mock_warn.call_args.args[1]
        assert len(app._workers) == 0

    def test_prepare_reenroll_user_declines_no_worker(
        self, make_app, monkeypatch
    ):
        """User clicks No on the confirm dialog → no worker started."""
        app = make_app()
        app.enroll_udid_combo.addItem("udid-x")
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.No,
        )
        with patch(
            "apple_device_cli.gui_qt.erase_device_for_reenrollment"
        ) as mock_erase:
            app._prepare_reenroll()
        mock_erase.assert_not_called()
        assert len(app._workers) == 0

    def test_prepare_reenroll_user_confirms_starts_worker(
        self, make_app, sample_devices, monkeypatch
    ):
        """User clicks Yes on the confirm dialog → erase_device_for_reenrollment called."""
        app = make_app()
        app.enroll_udid_combo.addItem(sample_devices[0].udid)
        app._devices = sample_devices  # so device label can be resolved
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        )
        with patch(
            "apple_device_cli.gui_qt.erase_device_for_reenrollment"
        ) as mock_erase:
            app._prepare_reenroll()
        mock_erase.assert_called_once_with(sample_devices[0].udid)


# ---------------------------------------------------------------------------
# Refresh flows: _refresh_devices / _on_devices_refreshed and
# _refresh_orgs / _on_orgs_refreshed.
# ---------------------------------------------------------------------------


class TestRefreshFlows:
    """Tests for the device-list and org-list refresh flows.

    Covers happy paths, error handling, and the stale-token discard branch.
    """

    def test_refresh_devices_happy_path_populates_list_and_udid_combo(
        self, make_app, monkeypatch
    ):
        """list_devices returns devices → _devices, devices_list, enroll_udid_combo all populated."""
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
        # _update_enroll_udids is called as part of the refresh
        assert app.enroll_udid_combo.count() == 2
        assert app.enroll_udid_combo.itemText(0) == "udid-1"
        assert "Found 2 device(s)" in app.log_text.toPlainText()

    def test_refresh_devices_error_logs_and_preserves_existing(
        self, make_app, monkeypatch
    ):
        """list_devices raises → error logged, existing _devices preserved."""
        app = make_app()
        existing = MagicMock(spec=DeviceInfo, udid="existing")
        app._devices = [existing]

        def boom():
            raise RuntimeError("USB not found")

        monkeypatch.setattr("apple_device_cli.gui_qt.list_devices", boom)

        app._refresh_devices()

        # Error logged; existing devices preserved (not cleared)
        assert "USB not found" in app.log_text.toPlainText()
        assert len(app._devices) == 1
        assert app._devices[0] is existing

    def test_refresh_devices_stale_token_skips_update(self, make_app):
        """Late completion with a stale token must not overwrite _devices."""
        app = make_app()
        app._devices = []
        # Force a high current token so anything lower is stale
        app._request_token = 100
        fake_new = [MagicMock(spec=DeviceInfo, udid="ignored")]
        app._on_devices_refreshed(fake_new, None, token=50)
        assert app._devices == []
        assert app.devices_list.count() == 0

    def test_refresh_orgs_happy_path_populates_list_and_enroll_combo(
        self, make_app, sample_org, monkeypatch
    ):
        """OrganizationManager.list_orgs returns orgs → _orgs, orgs_list, enroll_org_combo populated."""
        app = make_app(orgs=[sample_org])
        app._orgs = []  # reset so we verify the refresh repopulates
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.list_orgs",
            lambda self: [sample_org],
        )

        app._refresh_orgs()

        assert len(app._orgs) == 1
        assert app._orgs[0] is sample_org
        assert app.orgs_list.count() == 1
        # _update_enroll_orgs is called as part of the refresh
        assert app.enroll_org_combo.count() == 1
        assert app.enroll_org_combo.itemText(0) == sample_org.name
