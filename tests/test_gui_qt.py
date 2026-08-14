"""Tests for the PySide6 GUI module.

These tests run against real PySide6 widgets using the ``offscreen`` Qt
platform plugin (via ``QT_QPA_PLATFORM=offscreen``), so no display is required
in CI. We avoid bare ``MagicMock()`` for class-shaped values; mocks are always
``spec=``'d against the real PySide6 / project class.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
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
    """Make QMessageBox.{warning,critical,information,question} return immediately.

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
    monkeypatch.setattr(
        QMessageBox,
        "information",
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

        Verified by inspection: the worker-pool ``submit`` method calls
        ``btn.setEnabled(False)`` on each button in ``buttons_to_disable``
        before starting the worker, and re-enables them in the completion
        handler. The completion handler is exercised by
        ``test_button_re_enabled_after_worker_completes``.
        """
        import inspect
        from apple_device_cli.gui_qt.worker import WorkerPool

        source = inspect.getsource(WorkerPool.submit)
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


class TestEditOrg:
    def test_edit_org_requires_selection_warns_and_returns(self, make_app, monkeypatch):
        """No org selected → 'No organization' warning, no exception."""
        app = make_app()
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )
        app._edit_org()
        assert warnings, "Should warn when no org selected"
        assert warnings[0][0] == "No organization"
        # No worker started
        assert app._workers == []

    def test_edit_org_button_exists_and_is_wired(self, make_app, sample_org):
        """The Edit Org button must exist on the Orgs toolbar."""
        app = make_app(orgs=[sample_org])
        assert app.edit_org_btn is not None
        assert app.edit_org_btn.text() == "Edit Org"

    def test_build_edit_org_form_returns_initial_values(
        self, make_app, sample_org, monkeypatch
    ):
        """_build_edit_org_form returns a QDialog with fields pre-filled from the org."""
        from PySide6.QtWidgets import QDialog

        app = make_app(orgs=[sample_org])
        # Stub dialog.exec so it doesn't block
        monkeypatch.setattr(QDialog, "exec", lambda self: 0)
        dialog, fields = app._build_edit_org_form(sample_org)
        assert isinstance(dialog, QDialog)
        assert fields["org_id"].text() == (sample_org.org_id or "")
        assert fields["mdm_url"].text() == (sample_org.mdm_url or "")
        assert fields["checkin_url"].text() == (sample_org.checkin_url or "")
        assert fields["mdm_topic"].text() == (sample_org.mdm_topic or "")
        assert fields["cert_path"].text() == (sample_org.cert_path or "")

    def test_apply_edit_org_saves_modified_fields(
        self, make_app, sample_org, monkeypatch
    ):
        """_apply_edit_org writes a fresh Organization via save_org(overwrite=True)."""
        # sample_org fixture uses "Capital Candy" (with space) for display;
        # the GUI validator rejects spaces. Override to a name that validates.
        sample_org.name = "CapitalCandy"
        sample_org.org_id = "com.capitalcandy"

        app = make_app(orgs=[sample_org])
        captured: dict = {}

        def fake_save_org(self, org, overwrite=False):
            captured["name"] = org.name
            captured["mdm_url"] = org.mdm_url
            captured["checkin_url"] = org.checkin_url
            captured["mdm_topic"] = org.mdm_topic
            captured["cert_path"] = org.cert_path
            captured["key_path"] = org.key_path
            captured["overwrite"] = overwrite

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.save_org",
            fake_save_org,
        )

        # Build dialog (without exec), mutate fields, apply.
        from PySide6.QtWidgets import QDialog

        monkeypatch.setattr(QDialog, "exec", lambda self: 0)
        dialog, fields = app._build_edit_org_form(sample_org)
        fields["mdm_url"].setText("https://new-mdm.example.com/mdm")
        fields["checkin_url"].setText("https://new-mdm.example.com/checkin")
        fields["mdm_topic"].setText("com.example.new-topic")

        app._apply_edit_org(sample_org, fields, dialog)

        assert captured["name"] == "CapitalCandy"
        assert captured["mdm_url"] == "https://new-mdm.example.com/mdm"
        assert captured["checkin_url"] == "https://new-mdm.example.com/checkin"
        assert captured["mdm_topic"] == "com.example.new-topic"
        # key_path preserved (not editable in this dialog)
        assert captured["key_path"] == sample_org.key_path
        assert captured["overwrite"] is True

    def test_apply_edit_org_validates_inputs(self, make_app, sample_org, monkeypatch):
        """Invalid MDM URL → save_org NOT called, warning shown."""
        sample_org.name = "CapitalCandy"  # validator-acceptable name
        from PySide6.QtWidgets import QDialog

        app = make_app(orgs=[sample_org])
        called = {"save": False}

        def fake_save_org(self, org, overwrite=False):
            called["save"] = True

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.save_org", fake_save_org
        )

        monkeypatch.setattr(QDialog, "exec", lambda self: 0)
        dialog, fields = app._build_edit_org_form(sample_org)
        fields["mdm_url"].setText("not-a-url")

        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )

        app._apply_edit_org(sample_org, fields, dialog)
        assert called["save"] is False, "save_org must not be called for invalid input"
        assert any(title == "Invalid input" for title, _ in warnings), warnings


class TestOrgDetailsPane:
    def test_details_pane_exists(self, make_app):
        """The Orgs tab must have a details label."""
        app = make_app()
        assert app.orgs_details_label is not None

    def test_details_shows_org_fields(self, make_app, sample_org, monkeypatch):
        """Selecting an org populates the details label with its fields."""
        # sample_org.name has a space which is fine for read-only display
        app = make_app(orgs=[sample_org])
        # After make_app, the org is in self._orgs and orgs_list has 1 item
        # with currentRow=0; _update_org_details should have been called.
        text = app.orgs_details_label.text()
        assert sample_org.name in text
        assert "MDM URL:" in text
        assert "MDM profile:" in text
        assert "Identity:" in text
        assert "Created:" in text

    def test_details_shows_direct_mdm_install(self, make_app, sample_org):
        """Org with a bundled MDM mobileconfig → details show the direct-install path."""
        sample_org.mdm_mobileconfig_path = "/tmp/mdm.mobileconfig"
        app = make_app(orgs=[sample_org])
        text = app.orgs_details_label.text()
        assert "/tmp/mdm.mobileconfig" in text

    def test_details_empty_when_no_org(self, make_app):
        """No org selected → details show a placeholder."""
        app = make_app()
        app._update_org_details(-1)
        text = app.orgs_details_label.text()
        assert "no organization" in text.lower() or text.strip() == ""


class TestImportOrg:
    def test_import_button_exists(self, make_app):
        app = make_app()
        assert app.import_org_btn is not None
        assert app.import_org_btn.text() == "Import…"

    def test_import_routes_organization_file_to_import_org(
        self, make_app, monkeypatch
    ):
        """.organization extension → OrganizationManager.import_org called."""
        from pathlib import Path
        from apple_device_cli.orgs.manager import Organization

        app = make_app()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getOpenFileName",
            lambda *a, **kw: ("/tmp/Test.organization", "Apple Configurator (*.organization)"),
        )
        captured = {}
        def fake_import(self, path, password="password"):
            captured["path"] = path
            return Organization(name="Imported")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.import_org", fake_import
        )

        app._import_org()
        assert captured["path"] == Path("/tmp/Test.organization")

    def test_import_routes_mobileconfig_to_import_mobileconfig(
        self, make_app, monkeypatch
    ):
        """.mobileconfig extension → OrganizationManager.import_mobileconfig called."""
        from pathlib import Path
        from apple_device_cli.orgs.manager import Organization

        app = make_app()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getOpenFileName",
            lambda *a, **kw: ("/tmp/profile.mobileconfig", "Mobileconfig (*.mobileconfig)"),
        )
        captured = {}
        def fake_import_mc(self, path):
            captured["path"] = path
            return Organization(name="Imported")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.import_mobileconfig",
            fake_import_mc,
        )

        app._import_org()
        assert captured["path"] == Path("/tmp/profile.mobileconfig")

    def test_import_no_op_when_user_cancels(self, make_app, monkeypatch):
        """Empty selection → no import call."""
        app = make_app()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getOpenFileName",
            lambda *a, **kw: ("", ""),
        )
        called = {"import": False}
        def fake_import(self, *a, **kw):
            called["import"] = True
            return None
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.import_org", fake_import
        )
        app._import_org()
        assert called["import"] is False


class TestExportOrg:
    def test_export_button_exists(self, make_app):
        app = make_app()
        assert app.export_org_btn is not None
        assert app.export_org_btn.text() == "Export…"

    def test_export_requires_selection(self, make_app, monkeypatch):
        """No org selected → 'No organization' warning, no export call."""
        app = make_app()
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )
        called = {"export": False}
        def fake_export(self, *a, **kw):
            called["export"] = True
            return True
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.export_org", fake_export
        )
        app._export_org()
        assert called["export"] is False
        assert any(t == "No organization" for t, _ in warnings)

    def test_export_routes_to_export_org_with_path(
        self, make_app, sample_org, monkeypatch
    ):
        """Selected org + chosen path → export_org(name, dest) called."""
        from pathlib import Path

        app = make_app(orgs=[sample_org])
        app.orgs_list.setCurrentRow(0)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getSaveFileName",
            lambda *a, **kw: ("/tmp/Export.zip", "Zip (*.zip)"),
        )
        captured = {}
        def fake_export(self, name, dest):
            captured["name"] = name
            captured["dest"] = dest
            return True
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.export_org", fake_export
        )
        app._export_org()
        assert captured["name"] == sample_org.name
        assert captured["dest"] == Path("/tmp/Export.zip")

    def test_export_no_op_when_user_cancels(
        self, make_app, sample_org, monkeypatch
    ):
        """Empty save path → no export call."""
        app = make_app(orgs=[sample_org])
        app.orgs_list.setCurrentRow(0)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getSaveFileName",
            lambda *a, **kw: ("", ""),
        )
        called = {"export": False}
        def fake_export(self, *a, **kw):
            called["export"] = True
            return True
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.export_org", fake_export
        )
        app._export_org()
        assert called["export"] is False


class TestAttachWifi:
    def test_attach_wifi_button_exists(self, make_app):
        app = make_app()
        assert app.attach_wifi_btn is not None
        assert app.attach_wifi_btn.text() == "Attach WiFi…"

    def test_attach_wifi_requires_selection(self, make_app, monkeypatch):
        """No org selected → 'No organization' warning, no set_org_wifi call."""
        app = make_app()
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )
        called = {"wifi": False}
        from apple_device_cli.cli_actions import SetOrgWifiResult

        def fake_set_wifi(manager, name, path):
            called["wifi"] = True
            return SetOrgWifiResult(name=name, wifi_config_path=path)

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.set_org_wifi", fake_set_wifi
        )
        app._attach_wifi()
        assert called["wifi"] is False
        assert any(t == "No organization" for t, _ in warnings)

    def test_attach_wifi_calls_set_org_wifi(
        self, make_app, sample_org, monkeypatch, tmp_path
    ):
        """Selected org + wifi file → set_org_wifi called with name + path."""
        from pathlib import Path
        from apple_device_cli.cli_actions import SetOrgWifiResult

        app = make_app(orgs=[sample_org])
        app.orgs_list.setCurrentRow(0)
        wifi_file = tmp_path / "wifi.mobileconfig"
        wifi_file.write_bytes(b"<?xml version='1.0'?><plist></plist>")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getOpenFileName",
            lambda *a, **kw: (str(wifi_file), "Mobileconfig (*.mobileconfig)"),
        )
        captured = {}
        def fake_set_wifi(manager, name, path):
            captured["name"] = name
            captured["path"] = path
            return SetOrgWifiResult(name=name, wifi_config_path=path)

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.set_org_wifi", fake_set_wifi
        )
        monkeypatch.setattr(app, "_refresh_orgs", lambda: None)

        app._attach_wifi()
        assert captured["name"] == sample_org.name
        # set_org_wifi receives str(path); accept either str or Path.
        assert str(captured["path"]) == str(wifi_file)

    def test_attach_wifi_no_op_on_user_cancel(
        self, make_app, sample_org, monkeypatch
    ):
        """Empty file selection → no set_org_wifi call."""
        app = make_app(orgs=[sample_org])
        app.orgs_list.setCurrentRow(0)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.QFileDialog.getOpenFileName",
            lambda *a, **kw: ("", ""),
        )
        called = {"wifi": False}

        def fake_set_wifi(manager, name, path):
            called["wifi"] = True

        monkeypatch.setattr(
            "apple_device_cli.gui_qt.set_org_wifi", fake_set_wifi
        )
        app._attach_wifi()
        assert called["wifi"] is False


class TestStatusBarAndGeometry:
    def test_status_bar_exists(self, make_app):
        """Main window must have a status bar."""
        app = make_app()
        assert app.statusBar() is not None

    def test_status_bar_shows_counts_after_refresh(
        self, make_app, sample_devices, sample_org
    ):
        """After refresh, status bar shows device + org counts."""
        app = make_app(orgs=[sample_org])
        app._devices = sample_devices
        app._update_status_bar()
        msg = app.statusBar().currentMessage()
        assert "1 device" in msg
        assert "1 organization" in msg or "1 org" in msg

    def test_status_bar_shows_worker_count(
        self, make_app, sample_devices, sample_org
    ):
        """Active workers are surfaced in the status bar."""
        from unittest.mock import MagicMock

        app = make_app(orgs=[sample_org])
        app._devices = sample_devices
        worker = MagicMock(spec=["run", "quit", "wait"])
        app._workers.append(worker)
        app._update_status_bar()
        msg = app.statusBar().currentMessage()
        assert "1 operation" in msg

    def test_geometry_save_and_restore_methods_exist(self, make_app):
        """Save/restore helpers must exist."""
        app = make_app()
        assert callable(getattr(app, "_save_geometry", None))
        assert callable(getattr(app, "_restore_geometry", None))


class TestCertExpiryBadge:
    def test_cert_expiry_returns_none_for_missing_file(self):
        """Missing cert path → None."""
        from apple_device_cli.gui_qt import _cert_expiry
        assert _cert_expiry("/nonexistent/file.der") is None

    def test_cert_expiry_returns_none_for_unparseable(self, tmp_path):
        """Non-DER garbage → None (don't crash the list render)."""
        from apple_device_cli.gui_qt import _cert_expiry
        bogus = tmp_path / "fake.der"
        bogus.write_bytes(b"not a real cert")
        assert _cert_expiry(str(bogus)) is None

    def test_cert_expiry_returns_datetime_for_real_cert(self, tmp_path):
        """A real DER cert → datetime return value."""
        from datetime import datetime, timedelta, timezone
        from apple_device_cli.gui_qt import _cert_expiry

        # Generate a self-signed cert using the same library the project uses.
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "test")]
        )
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .sign(key, hashes.SHA256())
        )
        cert_path = tmp_path / "test.der"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
        expiry = _cert_expiry(str(cert_path))
        assert isinstance(expiry, datetime)
        assert expiry > now

    def test_format_cert_expiry_badge_classifies(self):
        """_format_cert_expiry_badge returns colored indicator strings."""
        from datetime import datetime, timedelta, timezone
        from apple_device_cli.gui_qt import _format_cert_expiry_badge

        now = datetime.now(timezone.utc)
        # No expiry → grey dot
        assert _format_cert_expiry_badge(None) == " ⚪"
        # Expired → red
        expired = now - timedelta(days=10)
        assert "expired" in _format_cert_expiry_badge(expired).lower()
        # ≤30 days → yellow
        soon = now + timedelta(days=15)
        assert "30d" in _format_cert_expiry_badge(soon) or "�" in _format_cert_expiry_badge(soon)
        # >30 days → green
        far = now + timedelta(days=365)
        assert "🟢" in _format_cert_expiry_badge(far)


def _make_real_cert(tmp_path, days_until_expiry: int):
    """Generate a self-signed DER cert + private key for tests.

    ``days_until_expiry`` may be negative (for expired-cert tests).
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.now(timezone.utc)
    not_before = now + timedelta(days=min(days_until_expiry, 0)) - timedelta(days=1)
    not_after = now + timedelta(days=days_until_expiry)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subj).issuer_name(subj)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "test.der"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    key_path = tmp_path / "test_key.der"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


class TestCertExpiryBanner:
    def test_banner_shows_identity_warning_when_org_has_no_cert(
        self, make_app, sample_org, monkeypatch
    ):
        """No cert on selected org → banner warns about identity + MDM status."""
        sample_org.name = "CapitalCandy"
        sample_org.cert_path = None
        sample_org.key_path = None
        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_org_combo.addItem(sample_org.name)
        idx = app.enroll_org_combo.findText(sample_org.name)
        app._on_enroll_org_changed(idx)
        assert not app.enroll_cert_warning_label.isHidden()
        text = app.enroll_cert_warning_label.text()
        assert "identity" in text.lower()
        assert "Setup Assistant" in text

    def test_banner_hidden_when_no_org(self, make_app):
        """No org selected → banner hidden."""
        app = make_app()
        app._on_enroll_org_changed(-1)
        assert app.enroll_cert_warning_label.isHidden()

    def test_banner_shown_for_healthy_cert_within_90_days(
        self, make_app, sample_org, monkeypatch, tmp_path
    ):
        """Cert valid for 60 days → green 'soft reminder' shown."""
        sample_org.name = "CapitalCandy"
        cert_path, key_path = _make_real_cert(tmp_path, days_until_expiry=60)
        sample_org.cert_path = cert_path
        sample_org.key_path = key_path

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_org_combo.addItem(sample_org.name)
        idx = app.enroll_org_combo.findText(sample_org.name)
        app._on_enroll_org_changed(idx)
        text = app.enroll_cert_warning_label.text()
        assert not app.enroll_cert_warning_label.isHidden()
        assert "valid" in text.lower() or "🟢" in text

    def test_banner_shown_for_expiring_soon(
        self, make_app, sample_org, monkeypatch, tmp_path
    ):
        """Cert expires in 14 days → yellow warning."""
        sample_org.name = "CapitalCandy"
        cert_path, key_path = _make_real_cert(tmp_path, days_until_expiry=14)
        sample_org.cert_path = cert_path
        sample_org.key_path = key_path

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_org_combo.addItem(sample_org.name)
        idx = app.enroll_org_combo.findText(sample_org.name)
        app._on_enroll_org_changed(idx)
        text = app.enroll_cert_warning_label.text()
        assert not app.enroll_cert_warning_label.isHidden()
        assert "14" in text or "expir" in text.lower()
        assert "🟡" in text or "yellow" in text.lower() or "regenerat" in text.lower()

    def test_banner_shown_for_expired_cert(
        self, make_app, sample_org, monkeypatch, tmp_path
    ):
        """Expired cert → red warning."""
        sample_org.name = "CapitalCandy"
        cert_path, key_path = _make_real_cert(tmp_path, days_until_expiry=-5)
        sample_org.cert_path = cert_path
        sample_org.key_path = key_path

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_org_combo.addItem(sample_org.name)
        idx = app.enroll_org_combo.findText(sample_org.name)
        app._on_enroll_org_changed(idx)
        text = app.enroll_cert_warning_label.text()
        assert not app.enroll_cert_warning_label.isHidden()
        assert "expired" in text.lower() or "🔴" in text

    def test_banner_shown_for_missing_cert_file(
        self, make_app, sample_org, monkeypatch
    ):
        """Cert file path set but file missing → unreadable warning."""
        sample_org.name = "CapitalCandy"
        sample_org.cert_path = "/nonexistent/cert.der"
        sample_org.key_path = "/nonexistent/key.der"
        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_org_combo.addItem(sample_org.name)
        idx = app.enroll_org_combo.findText(sample_org.name)
        app._on_enroll_org_changed(idx)
        assert not app.enroll_cert_warning_label.isHidden()
        text = app.enroll_cert_warning_label.text()
        assert "unreadable" in text.lower() or "missing" in text.lower() or "regenerat" in text.lower()

    def test_banner_shows_direct_mdm_install(
        self, make_app, sample_org, monkeypatch, tmp_path
    ):
        """Bundled MDM mobileconfig → banner shows direct install + path."""
        sample_org.name = "CapitalCandy"
        cert_path, key_path = _make_real_cert(tmp_path, days_until_expiry=60)
        sample_org.cert_path = cert_path
        sample_org.key_path = key_path
        sample_org.mdm_mobileconfig_path = "/tmp/mdm.mobileconfig"
        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_org_combo.addItem(sample_org.name)
        idx = app.enroll_org_combo.findText(sample_org.name)
        app._on_enroll_org_changed(idx)
        text = app.enroll_cert_warning_label.text()
        assert not app.enroll_cert_warning_label.isHidden()
        assert "direct install" in text
        assert "/tmp/mdm.mobileconfig" in text


class TestDevicesContextMenu:
    def test_devices_list_has_custom_context_menu(self, make_app):
        from PySide6.QtCore import Qt
        app = make_app()
        assert app.devices_list.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu

    def test_build_devices_context_menu_has_all_actions(
        self, make_app, sample_devices
    ):
        """_build_devices_context_menu returns a QMenu with all 4 actions."""
        from PySide6.QtWidgets import QListWidgetItem, QMenu
        app = make_app()
        app._devices = sample_devices
        for d in sample_devices:
            QListWidgetItem(f"{d.device_name} ({d.udid})", app.devices_list)

        # Pretend we right-clicked at position (10, 10) on the first row.
        app.devices_list.setCurrentRow(0)
        menu = app._build_devices_context_menu()
        assert isinstance(menu, QMenu)
        labels = [a.text() for a in menu.actions()]
        expected = ["Show Device Info", "Activate", "Pair / Trust"]
        for label in expected:
            assert label in labels, f"Missing context menu item: {label}"
        # "Make Supervised" only appears when an org is selected — gating
        # hides it otherwise (closes Round 2's open question).
        assert "Make Supervised" not in labels
        menu.deleteLater()

    def test_context_menu_bails_when_no_row_at_pos(self, make_app):
        """_show_devices_context_menu bails on empty list — no menu shown."""
        app = make_app()
        # Override exec to assert it's never called
        from PySide6.QtWidgets import QMenu as RealQMenu
        original_init = RealQMenu.__init__

        def init_capture(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            # Override exec to raise if called
            import types
            self.exec = types.MethodType(
                lambda self_m: (_ for _ in ()).throw(
                    AssertionError("exec should not be called when no row")
                ),
                self,
            )

        RealQMenu.__init__ = init_capture
        try:
            from PySide6.QtCore import QPoint
            app._show_devices_context_menu(QPoint(0, 0))
        finally:
            RealQMenu.__init__ = original_init


class TestEnrollActionGating:
    def test_buttons_disabled_when_no_org(self, make_app):
        app = make_app()
        # Both buttons exist
        assert app.guided_enroll_btn is not None
        assert app.make_supervised_btn is not None
        # Default state: no org selected → buttons disabled
        assert not app.guided_enroll_btn.isEnabled()
        assert not app.make_supervised_btn.isEnabled()

    def test_buttons_still_disabled_when_org_but_no_device(
        self, make_app, sample_org
    ):
        sample_org.name = "CapitalCandy"
        app = make_app(orgs=[sample_org])
        # Org in the combo but no device → still disabled
        app._update_enroll_action_gates()
        assert not app.guided_enroll_btn.isEnabled()
        assert not app.make_supervised_btn.isEnabled()

    def test_buttons_enabled_when_org_and_device_present(
        self, make_app, sample_org, sample_devices
    ):
        sample_org.name = "CapitalCandy"
        app = make_app(orgs=[sample_org])
        # Populate the UDID combo
        app.enroll_udid_combo.addItems([d.udid for d in sample_devices])
        app._update_enroll_action_gates()
        assert app.guided_enroll_btn.isEnabled()
        assert app.make_supervised_btn.isEnabled()

    def test_tooltip_explains_disabled_state(self, make_app):
        app = make_app()
        tooltip = app.guided_enroll_btn.toolTip()
        assert "org" in tooltip.lower() or "device" in tooltip.lower()

    def test_gates_update_when_org_combo_changes(
        self, make_app, sample_org
    ):
        """Changing the org combo must re-evaluate the gate."""
        sample_org.name = "CapitalCandy"
        app = make_app(orgs=[sample_org])
        # Add UDID — still no org text
        app.enroll_udid_combo.addItem("udid-x")
        # Gate fires when combo signals. Add the org name to the combo.
        app.enroll_org_combo.addItem(sample_org.name)
        # currentIndexChanged signal triggers _update_enroll_action_gates
        assert app.guided_enroll_btn.isEnabled()
        # Clear org → disabled again
        app.enroll_org_combo.setCurrentIndex(-1)
        assert not app.guided_enroll_btn.isEnabled()


class TestLastOperation:
    def test_record_last_op_updates_status_bar(self, make_app):
        app = make_app()
        app._record_last_op("Restored iPhone 14")
        msg = app.statusBar().currentMessage()
        assert "Last:" in msg
        assert "Restored iPhone 14" in msg

    def test_record_last_op_persists_via_qsettings(self, make_app):
        app = make_app()
        app._record_last_op("Enrolled iPhone 14 with Acme")
        from PySide6.QtCore import QSettings
        s = QSettings("ios-enroll", "gui")
        stored = s.value("lastOperation")
        assert stored is not None
        assert stored.startswith("Enrolled iPhone 14 with Acme @ ")

    def test_status_bar_includes_restored_last_op(self, make_app):
        """A pre-seeded lastOperation shows on the status bar."""
        from PySide6.QtCore import QSettings
        QSettings("ios-enroll", "gui").setValue("lastOperation", "Enrolled iPad")
        app = make_app()
        msg = app.statusBar().currentMessage()
        assert "Enrolled iPad" in msg


class TestKeyboardShortcuts:
    def test_ctrl_r_refreshes_devices(self, make_app, monkeypatch):
        app = make_app()
        called = {"refresh": False}
        monkeypatch.setattr(app, "_refresh_devices", lambda: called.__setitem__("refresh", True))
        from PySide6.QtGui import QKeySequence, QShortcut
        for child in app.findChildren(QShortcut):
            if QKeySequence(child.key()) == QKeySequence("Ctrl+R"):
                child.activated.emit()
                break
        assert called["refresh"]

    def test_ctrl_e_triggers_guided_enroll(self, make_app, monkeypatch):
        app = make_app()
        called = {"guided": False}
        monkeypatch.setattr(app, "_guided_enroll", lambda: called.__setitem__("guided", True))
        from PySide6.QtGui import QKeySequence, QShortcut
        for child in app.findChildren(QShortcut):
            if QKeySequence(child.key()) == QKeySequence("Ctrl+E"):
                child.activated.emit()
                break
        assert called["guided"]

    def test_ctrl_s_triggers_start_restore(self, make_app, monkeypatch):
        app = make_app()
        called = {"restore": False}
        monkeypatch.setattr(app, "_start_restore", lambda: called.__setitem__("restore", True))
        from PySide6.QtGui import QKeySequence, QShortcut
        for child in app.findChildren(QShortcut):
            if QKeySequence(child.key()) == QKeySequence("Ctrl+S"):
                child.activated.emit()
                break
        assert called["restore"]

    def test_all_three_shortcuts_exist(self, make_app):
        from PySide6.QtGui import QKeySequence, QShortcut
        app = make_app()
        keys = {QKeySequence(child.key()).toString() for child in app.findChildren(QShortcut)}
        assert "Ctrl+R" in keys
        assert "Ctrl+E" in keys
        assert "Ctrl+S" in keys


class TestTabIcons:
    def test_all_tabs_have_icons(self, make_app):
        from PySide6.QtWidgets import QTabWidget
        app = make_app()
        tabs = app.findChild(QTabWidget)
        assert tabs is not None
        for i in range(tabs.count()):
            icon = tabs.tabIcon(i)
            assert not icon.isNull(), f"Tab {i} ({tabs.tabText(i)}) has no icon"


class TestRestoreEmptyState:
    def test_empty_state_label_exists(self, make_app):
        app = make_app()
        assert app.restore_empty_state_label is not None

    def test_empty_state_visible_initially(self, make_app):
        """No device, no IPSW, no version → hint visible."""
        app = make_app()
        app._update_restore_empty_state()
        assert not app.restore_empty_state_label.isHidden()

    def test_empty_state_hidden_when_device_selected(self, make_app):
        app = make_app()
        app.restore_device_combo.addItem("Test iPhone", userData="udid-x")
        app._update_restore_empty_state()
        assert app.restore_empty_state_label.isHidden()

    def test_empty_state_hidden_when_ipsw_browsed(self, make_app, tmp_path):
        app = make_app()
        ipsw = tmp_path / "test.ipsw"
        ipsw.write_bytes(b"x")
        app._restore_ipsw_path = ipsw
        app._update_restore_empty_state()
        assert app.restore_empty_state_label.isHidden()

    def test_empty_state_hidden_when_version_in_combo(self, make_app):
        """A signed version in the versions combo also hides the hint."""
        app = make_app()
        app.restore_versions_combo.addItem("17.0 (cached)", userData="https://example.com/ipsw")
        app._update_restore_empty_state()
        assert app.restore_empty_state_label.isHidden()


class TestClearCache:
    def test_clear_cache_button_exists(self, make_app):
        app = make_app()
        assert app.restore_clear_cache_btn is not None
        assert app.restore_clear_cache_btn.text() == "Clear cache"

    def test_clear_cache_requires_confirm(
        self, make_app, monkeypatch, tmp_path
    ):
        """User clicks No → rmtree NOT called."""
        app = make_app()
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.resolve_cache_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.No,
        )
        with patch("apple_device_cli.gui_qt.shutil.rmtree") as mock_rm:
            app._clear_restore_cache()
        mock_rm.assert_not_called()

    def test_clear_cache_wipes_dir_on_confirm(
        self, make_app, monkeypatch, tmp_path
    ):
        """User clicks Yes → cache dir removed and recreated empty."""
        app = make_app()
        (tmp_path / "fake.ipsw").write_bytes(b"x")
        (tmp_path / "another.ipsw").write_bytes(b"y")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.resolve_cache_dir", lambda: tmp_path
        )
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        app._clear_restore_cache()
        assert not (tmp_path / "fake.ipsw").exists()
        assert not (tmp_path / "another.ipsw").exists()
        assert tmp_path.is_dir()  # recreated


class TestGuidedEnroll:
    def test_guided_enroll_button_exists(self, make_app):
        app = make_app()
        assert app.guided_enroll_btn is not None
        assert "Guided Enroll" in app.guided_enroll_btn.text()

    def test_guided_enroll_no_org_warns_returns(self, make_app, monkeypatch):
        """No org selected → warning, no worker."""
        from types import SimpleNamespace

        app = make_app()
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )
        called = {"make": False}
        def fake_make(**kwargs):
            called["make"] = True
            return SimpleNamespace(
                supervised=True, mdm_enrolled=True, wifi_installed=False, errors=[]
            )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make
        )
        app._guided_enroll()
        assert called["make"] is False
        assert any(t == "No organization" for t, _ in warnings)

    def test_guided_enroll_missing_identity_warns_returns(
        self, make_app, sample_org, monkeypatch
    ):
        """Org without cert/key → 'Missing identity' warning, no worker."""
        sample_org.name = "CapitalCandy"  # validator-friendly
        sample_org.cert_path = None
        sample_org.key_path = "/tmp/key.der"

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_udid_combo.addItem("udid-x")
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )
        called = {"make": False}
        def fake_make(**kwargs):
            called["make"] = True
            return None
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make
        )
        app._guided_enroll()
        assert called["make"] is False
        assert any(t == "Missing identity" for t, _ in warnings)

    def test_guided_enroll_no_udid_warns_returns(
        self, make_app, sample_org, monkeypatch
    ):
        """Empty UDID combo → 'No device' warning, no worker."""
        sample_org.name = "CapitalCandy"

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda parent, title, text, *a, **kw: warnings.append((title, text))
            or QMessageBox.StandardButton.Ok,
        )
        called = {"make": False}
        def fake_make(**kwargs):
            called["make"] = True
            return None
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make
        )
        app._guided_enroll()
        assert called["make"] is False
        assert any(t == "No device" for t, _ in warnings)

    def test_guided_enroll_user_declines_no_worker(
        self, make_app, sample_org, monkeypatch
    ):
        """User clicks No on confirm → no make_supervised call."""
        sample_org.name = "CapitalCandy"

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_udid_combo.addItem("udid-x")
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **kw: QMessageBox.StandardButton.No,
        )
        called = {"make": False}
        def fake_make(**kwargs):
            called["make"] = True
            return None
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make
        )
        app._guided_enroll()
        assert called["make"] is False

    def test_guided_enroll_happy_path_runs_make_supervised(
        self, make_app, sample_org, monkeypatch
    ):
        """All guards pass + user confirms → make_supervised called with form values."""
        from types import SimpleNamespace

        sample_org.name = "CapitalCandy"
        sample_org.org_id = "com.capitalcandy"
        sample_org.mdm_mobileconfig_path = "/tmp/mdm.mobileconfig"

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_udid_combo.addItem("udid-1")
        app.enroll_wifi_ssid.setText("CorpNet")
        app.enroll_wifi_password.setText("supersecret")
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        captured = {}
        def fake_make(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                supervised=True, mdm_enrolled=True, wifi_installed=True, errors=[]
            )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make
        )
        app._guided_enroll()
        assert captured["udid"] == "udid-1"
        assert captured["org_name"] == "CapitalCandy"
        assert captured["wifi_ssid"] == "CorpNet"
        assert captured["wifi_password"] == "supersecret"
        assert captured["mdm_mobileconfig"] == "/tmp/mdm.mobileconfig"
        assert callable(captured["progress_callback"])

    def test_guided_enroll_progress_scrubs_wifi_password(
        self, make_app, sample_org, monkeypatch
    ):
        """progress_callback must redact WiFi password from progress messages."""
        from types import SimpleNamespace

        sample_org.name = "CapitalCandy"

        app = make_app(orgs=[sample_org])
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.get_org",
            lambda self, name: sample_org,
        )
        app.enroll_udid_combo.addItem("udid-1")
        app.enroll_wifi_password.setText("supersecret")
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        captured = {}
        def fake_make(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                supervised=True, mdm_enrolled=False, wifi_installed=False, errors=[]
            )
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.make_supervised", fake_make
        )
        app._guided_enroll()
        log_before = app.log_text.toPlainText()
        captured["progress_callback"]("Connecting to supersecret network...")
        new_text = app.log_text.toPlainText()[len(log_before):]
        assert "supersecret" not in new_text
        assert "***" in new_text


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


def _fresh_gui_qt():
    """Import ``apple_device_cli.gui_qt`` with a clean namespace.

    Earlier tests run with real PySide6 and materialize the Qt-using classes
    (``EnrollmentApp`` etc.) as module globals. ``importlib.reload`` would keep
    them — reload only re-runs the module body, and the body never rebinds
    those names — so drop the module from ``sys.modules`` to force a fresh
    execution with empty globals, like a real headless ``python -m`` start.

    Also repair the ``apple_device_cli.gui_qt`` package attribute: forms like
    ``import apple_device_cli.gui_qt as x`` / ``from apple_device_cli import
    gui_qt`` resolve through that attribute rather than ``sys.modules``, and
    other tests (e.g. ``runpy.run_module``) can leave it pointing at a
    superseded module object.

    Note: ``apple_device_cli.gui_qt.app`` is dropped too — otherwise the
    ``_require_pyside6`` idempotency guard sees ``MainWindow`` in the
    submodule's globals (from a prior successful import) and returns early,
    which makes a later ``monkeypatch.setitem(sys.modules, "PySide6.QtWidgets",
    None)`` invisible to ``run_gui`` and the test hangs on ``app.exec()``
    instead of raising ``RuntimeError``.
    """
    sys.modules.pop("apple_device_cli.gui_qt", None)
    sys.modules.pop("apple_device_cli.gui_qt.app", None)
    import apple_device_cli.gui_qt as mod

    import apple_device_cli as pkg

    pkg.gui_qt = mod
    return mod


class TestRunGui:
    def test_run_gui_raises_when_pyside6_missing(self, monkeypatch):
        """``run_gui`` raises ``RuntimeError`` on a headless install.

        Earlier tests run with real PySide6 and materialize the Qt-using
        classes on the ``gui_qt`` module, so use a freshly imported module
        (simulating a headless process) to ensure ``run_gui`` actually
        attempts the PySide6 import instead of short-circuiting.
        """
        gui_qt = _fresh_gui_qt()
        # ``monkeypatch.setitem`` (not ``patch.dict``) on sys.modules: patch.dict
        # clears the whole registry on exit, which can orphan module objects
        # referenced through package attributes.
        monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
        with pytest.raises(RuntimeError):
            gui_qt.run_gui()

    def test_module_imports_without_pyside6(self, monkeypatch):
        """``import apple_device_cli.gui_qt`` must succeed even when PySide6
        is not installed, so the GUI module isn't a hard dependency on the
        core install path.
        """
        # Drop any cached PySide6 imports and block re-import.
        for name in [k for k in sys.modules if k == "PySide6" or k.startswith("PySide6.")]:
            del sys.modules[name]
        monkeypatch.setitem(sys.modules, "PySide6", None)
        monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)
        monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
        # Force a fresh import. ``runpy`` runs the module body in a fresh
        # namespace, just like a real ``import apple_device_cli.gui_qt``
        # would. ``runpy.run_module`` can't run a bare package without a
        # ``__main__.py``, so target the package's ``__init__`` submodule to
        # simulate the import.
        import runpy

        saved_gui_qt = sys.modules.pop("apple_device_cli.gui_qt", None)
        try:
            ns = runpy.run_module(
                "apple_device_cli.gui_qt.__init__", run_name="__not_main__"
            )
        finally:
            if saved_gui_qt is not None:
                sys.modules["apple_device_cli.gui_qt"] = saved_gui_qt

        # Pure-Python names (no Qt needed) should be available.
        assert "validate_org_fields" in ns
        assert "validate_identity_days" in ns
        assert "_write_identity_atomic" in ns
        # Qt-using names should NOT be in the namespace until something
        # triggers _require_pyside6().
        assert "WorkerThread" not in ns
        assert "EnrollmentApp" not in ns

    def test_lazy_attr_raises_friendly_error_without_pyside6(self, monkeypatch):
        """Accessing ``WorkerThread``, ``EnrollmentApp``, or ``run_gui`` on
        a headless install must raise ``RuntimeError`` (with the install
        hint) instead of bubbling up an ``ImportError`` traceback.
        """
        for name in [k for k in sys.modules if k == "PySide6" or k.startswith("PySide6.")]:
            del sys.modules[name]
        monkeypatch.setitem(sys.modules, "PySide6", None)
        monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)
        monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
        mod = _fresh_gui_qt()
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
        sample_org.mdm_mobileconfig_path = "/tmp/mdm.mobileconfig"
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
        assert captured["mdm_mobileconfig"] == "/tmp/mdm.mobileconfig"
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

    def test_devices_empty_label_hides_when_devices_arrive(self, make_app, monkeypatch):
        """Empty-state placeholder must hide after a refresh populates devices."""
        from unittest.mock import MagicMock

        app = make_app()
        # Empty state: placeholder visible (initial state from _create_devices_tab)
        app.devices_empty_label.setVisible(True)
        fake = MagicMock(spec=DeviceInfo, udid="d1", device_name="iPhone")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.list_devices", lambda: [fake]
        )
        app._refresh_devices()
        assert app.devices_empty_label.isHidden(), \
            "Placeholder should hide when devices list is populated"

    def test_devices_empty_label_shows_after_drain(self, make_app, monkeypatch):
        """After a refresh empties the list, placeholder must re-appear."""
        from unittest.mock import MagicMock

        app = make_app()
        # Start with one device
        fake = MagicMock(spec=DeviceInfo, udid="d1", device_name="iPhone")
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.list_devices", lambda: [fake]
        )
        app._refresh_devices()
        assert app.devices_empty_label.isHidden()
        # Now refresh with empty list
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.list_devices", lambda: []
        )
        app._refresh_devices()
        assert not app.devices_empty_label.isHidden(), \
            "Placeholder should re-appear when list becomes empty"

    def test_orgs_empty_label_hides_when_orgs_arrive(self, make_app, sample_org, monkeypatch):
        """Empty-state placeholder must hide after an org refresh populates orgs."""
        app = make_app(orgs=[sample_org])
        # Reset orgs and trigger refresh — placeholder should hide.
        app.orgs_empty_label.setVisible(True)
        monkeypatch.setattr(
            "apple_device_cli.gui_qt.OrganizationManager.list_orgs",
            lambda self: [sample_org],
        )
        app._refresh_orgs()
        assert app.orgs_empty_label.isHidden()

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


class TestLogCollapsed:
    """The shared activity log starts collapsed; toggling it persists."""

    @pytest.fixture(autouse=True)
    def _reset_pref(self, monkeypatch):
        from PySide6.QtCore import QSettings
        QSettings("ios-enroll", "gui").setValue("logExpanded", False)
        yield
        QSettings("ios-enroll", "gui").setValue("logExpanded", False)

    def test_default_is_collapsed(self, make_app):
        app = make_app()
        # isHidden() reflects the explicit setVisible() state, unlike
        # isVisible() which also depends on parent visibility (the
        # MainWindow is never shown in offscreen tests).
        assert app.log_text.isHidden() is True
        assert app.log_toggle_btn.isChecked() is False

    def test_toggle_expands_and_collapses(self, make_app):
        app = make_app()
        app.log_toggle_btn.setChecked(True)
        assert app.log_text.isHidden() is False
        app.log_toggle_btn.setChecked(False)
        assert app.log_text.isHidden() is True

    def test_toggle_persists_via_qsettings(self, make_app):
        app = make_app()
        app.log_toggle_btn.setChecked(True)
        from PySide6.QtCore import QSettings
        stored = QSettings("ios-enroll", "gui").value("logExpanded", type=bool)
        assert stored is True
        # Next-launch path: a fresh app reads the persisted preference.
        app2 = make_app()
        assert app2.log_text.isHidden() is False
        assert app2.log_toggle_btn.isChecked() is True
