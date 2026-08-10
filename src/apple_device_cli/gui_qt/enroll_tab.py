"""EnrollTab — controller for the Enrollment tab of the GUI.

Extracted from ``EnrollmentApp`` in Round 3 of the GUI refactor. Owns
the Enrollment tab widgets (org/preset/udid combos, WiFi fields, action
buttons, cert-expiry banner) and the action handlers (validate prereqs,
make supervised, guided enroll, check status, prepare re-enroll).

Cross-tab data sources:
* ``enroll_org_combo`` is populated by ``_update_enroll_orgs`` (still on
  the shell today, moves to OrgsTab on its own refresh in a future step).
* ``enroll_udid_combo`` is populated by ``_update_enroll_udids`` (still
  on the shell, will move to DevicesTab).
* ``enroll_cert_warning_label`` is updated by ``_update_enroll_cert_banner``
  (still on the shell, will move with the org combo).

Back-compat shims on ``EnrollmentApp`` (set in ``app.py``) forward
attribute reads for the enroll widgets and method calls to this
controller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFormLayout,
)
from PySide6.QtCore import Qt

from apple_device_cli.core.redaction import sanitize_text
from apple_device_cli.enrollment.supervised import (
    erase_device_for_reenrollment,
    get_device_enrollment_state,
    make_supervised,
    validate_enrollment_prerequisites,
)
from apple_device_cli.orgs.manager import Organization

if TYPE_CHECKING:
    from apple_device_cli.gui_qt.app import EnrollmentApp


def _resolve_skip_panes(preset, extra_panes):
    from apple_device_cli import gui_qt

    return gui_qt.resolve_skip_panes(preset, extra_panes)


def _redact_in_text(text, secret):
    from apple_device_cli import gui_qt

    return gui_qt._redact_in_text(text, secret)


def _validate_enrollment_prerequisites(**kwargs):
    from apple_device_cli import gui_qt

    return gui_qt.validate_enrollment_prerequisites(**kwargs)


def _make_supervised(**kwargs):
    from apple_device_cli import gui_qt

    return gui_qt.make_supervised(**kwargs)


def _get_device_enrollment_state(udid):
    from apple_device_cli import gui_qt

    return gui_qt.get_device_enrollment_state(udid)


def _erase_device_for_reenrollment(udid):
    from apple_device_cli import gui_qt

    return gui_qt.erase_device_for_reenrollment(udid)


def _cert_expiry(path):
    from apple_device_cli import gui_qt

    return gui_qt._cert_expiry(path)


def _organization_manager():
    from apple_device_cli import gui_qt

    return gui_qt.OrganizationManager()


class EnrollTab:
    """Owns the Enrollment tab widgets and action logic."""

    def __init__(self, shell: "EnrollmentApp") -> None:
        self._shell = shell
        self._manager = _organization_manager()
        self.widget = self._build()

    # -- TabController protocol ------------------------------------------

    def tab_widget(self) -> QWidget:
        return self.widget

    def refresh(self) -> None:
        return None

    def on_org_changed(self, org: Any) -> None:
        return None

    def on_device_changed(self, device: Any) -> None:
        return None

    # -- Build -----------------------------------------------------------

    def _build(self) -> QWidget:
        from apple_device_cli.enrollment.skip_panes import PRESETS

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ----- Organization & device groupbox -----
        org_box = QGroupBox("Organization & device")
        org_form = QFormLayout(org_box)
        org_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.enroll_org_combo = QComboBox()
        org_form.addRow("Organization:", self.enroll_org_combo)

        self.enroll_preset_combo = QComboBox()
        self.enroll_preset_combo.addItems(list(PRESETS.keys()))
        self.enroll_preset_combo.setCurrentText("standard")
        org_form.addRow("Skip preset:", self.enroll_preset_combo)

        udid_row = QHBoxLayout()
        self.enroll_udid_combo = QComboBox()
        udid_row.addWidget(self.enroll_udid_combo, 1)
        use_device_btn = QPushButton("Use Selected Device")
        use_device_btn.clicked.connect(self._shell._use_selected_device)
        udid_row.addWidget(use_device_btn)
        org_form.addRow("Device UDID:", udid_row)

        layout.addWidget(org_box)

        self.enroll_cert_warning_label = QLabel("")
        self.enroll_cert_warning_label.setWordWrap(True)
        self.enroll_cert_warning_label.setStyleSheet(
            "padding: 6px 8px; border-radius: 4px; font-weight: 600;"
        )
        self.enroll_cert_warning_label.setVisible(False)
        layout.addWidget(self.enroll_cert_warning_label)

        # ----- WiFi groupbox -----
        wifi_box = QGroupBox("WiFi (optional)")
        wifi_form = QFormLayout(wifi_box)
        wifi_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.enroll_wifi_ssid = QLineEdit()
        wifi_form.addRow("SSID:", self.enroll_wifi_ssid)

        self.enroll_wifi_password = QLineEdit()
        self.enroll_wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
        wifi_form.addRow("Password:", self.enroll_wifi_password)

        self.enroll_wifi_enc = QComboBox()
        self.enroll_wifi_enc.addItems(["WPA", "WEP", "None"])
        self.enroll_wifi_enc.setCurrentText("WPA")
        wifi_form.addRow("Encryption:", self.enroll_wifi_enc)

        layout.addWidget(wifi_box)

        # ----- Actions row -----
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.guided_enroll_btn = QPushButton("Guided Enroll")
        self.guided_enroll_btn.setObjectName("guided_enroll_btn")
        f = self.guided_enroll_btn.font()
        f.setBold(True)
        self.guided_enroll_btn.setFont(f)
        self.guided_enroll_btn.setMinimumHeight(36)
        self.guided_enroll_btn.setToolTip(
            "Validate prerequisites, then enroll the selected device "
            "with the selected org in one click."
        )
        self.guided_enroll_btn.clicked.connect(self._guided_enroll)
        actions_row.addWidget(self.guided_enroll_btn)

        self.make_supervised_btn = QPushButton("Make Supervised")
        self.make_supervised_btn.setObjectName("make_supervised_btn")
        f = self.make_supervised_btn.font()
        f.setBold(True)
        self.make_supervised_btn.setFont(f)
        self.make_supervised_btn.setMinimumHeight(36)
        self.make_supervised_btn.clicked.connect(self._make_supervised)
        actions_row.addWidget(self.make_supervised_btn)

        actions_row.addSpacing(16)

        self.validate_btn = QPushButton("Validate")
        self.validate_btn.clicked.connect(self._validate_prereqs)
        actions_row.addWidget(self.validate_btn)

        self.check_status_btn = QPushButton("Check Status")
        self.check_status_btn.clicked.connect(self._check_status)
        actions_row.addWidget(self.check_status_btn)

        self.prepare_reenroll_btn = QPushButton("Prepare Re-Enroll")
        self.prepare_reenroll_btn.clicked.connect(self._prepare_reenroll)
        actions_row.addWidget(self.prepare_reenroll_btn)

        actions_row.addStretch()
        layout.addLayout(actions_row)

        layout.addStretch(1)
        return widget

    # -- Org resolution --------------------------------------------------

    def _resolve_enroll_org(self) -> Organization | None:
        name = self.enroll_org_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self._shell, "No organization", "Select an organization.")
            return None
        # Defer to package-level lookup so tests can monkeypatch
        # ``apple_device_cli.gui_qt.OrganizationManager`` per-call.
        org = _organization_manager().get_org(name)
        if not org:
            QMessageBox.warning(self._shell, "Unknown organization", f"Organization not found: {name}")
            return None
        return org

    # -- Action gating ---------------------------------------------------

    def _update_enroll_action_gates(self) -> None:
        """Enable Guided Enroll + Make Supervised only when org + UDID are present."""
        has_org = self.enroll_org_combo.count() > 0 and bool(
            self.enroll_org_combo.currentText().strip()
        )
        has_device = self.enroll_udid_combo.count() > 0 and bool(
            self.enroll_udid_combo.currentText().strip()
        )
        enabled = has_org and has_device
        self.guided_enroll_btn.setEnabled(enabled)
        self.make_supervised_btn.setEnabled(enabled)
        if not has_org:
            tip = "Select an organization in the Organizations tab, then a device here."
        elif not has_device:
            tip = "Connect an iOS device and click Refresh Devices, then select one here."
        else:
            tip = (
                "Validate prerequisites, then enroll the selected device "
                "with the selected org in one click."
            )
        self.guided_enroll_btn.setToolTip(tip)
        self.make_supervised_btn.setToolTip(
            tip if enabled else "Same prerequisites as Guided Enroll."
        )

    def _update_enroll_cert_banner(self, org: Organization | None) -> None:
        label = self.enroll_cert_warning_label
        if org is None or not (org.cert_path and org.key_path):
            label.setVisible(False)
            return
        expiry = _cert_expiry(org.cert_path)
        if expiry is None:
            label.setText(
                "⚠  Cert file is missing or unreadable — "
                "regenerate identity before enrolling."
            )
            label.setStyleSheet(
                "padding: 6px 8px; border-radius: 4px; font-weight: 600;"
                "background: #fdecea; color: #b71c1c;"
            )
            label.setVisible(True)
            return
        from datetime import datetime, timezone

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days_left = (expiry - now).days
        if days_left < 0:
            label.setText(
                f"🔴  Cert expired {-days_left} day(s) ago — "
                "regenerate identity before enrolling."
            )
            label.setStyleSheet(
                "padding: 6px 8px; border-radius: 4px; font-weight: 600;"
                "background: #fdecea; color: #b71c1c;"
            )
            label.setVisible(True)
        elif days_left <= 30:
            label.setText(
                f"🟡  Cert expires in {days_left} day(s) — "
                "consider regenerating identity."
            )
            label.setStyleSheet(
                "padding: 6px 8px; border-radius: 4px; font-weight: 600;"
                "background: #fff8e1; color: #8d6e00;"
            )
            label.setVisible(True)
        elif days_left <= 90:
            label.setText(f"🟢  Cert valid for {days_left} more days.")
            label.setStyleSheet(
                "padding: 6px 8px; border-radius: 4px;"
                "background: #e8f5e9; color: #2e7d32;"
            )
            label.setVisible(True)
        else:
            label.setVisible(False)

    # -- Validate / Make Supervised / Guided Enroll ---------------------

    def _validate_prereqs(self) -> None:
        org = self._resolve_enroll_org()
        if not org:
            return
        self._shell._log(f"Validating prerequisites for {org.name}...")

        def work() -> list[str]:
            return _validate_enrollment_prerequisites(
                cert_path=org.cert_path,
                key_path=org.key_path,
                org_name=org.name,
                mdm_url=org.mdm_url,
                check_mdm_reachability=False,
            )

        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(work)
        self._shell._run_worker(worker, self._on_validation_result, [self.validate_btn])

    @Slot(object, object)
    def _on_validation_result(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Validation failed: {error}")
            return
        errors = result or []
        if errors:
            self._shell._log("Validation failed:")
            for err in errors:
                self._shell._log(f"  - {err}")
        else:
            self._shell._log("All prerequisites valid.")

    def _make_supervised(self) -> None:
        org = self._resolve_enroll_org()
        if not org:
            return
        if not org.cert_path or not org.key_path:
            QMessageBox.warning(
                self._shell,
                "Missing identity",
                f"Organization '{org.name}' needs a supervising certificate and key. "
                "Generate one in the Organizations tab first.",
            )
            return
        udid = self.enroll_udid_combo.currentText().strip()
        if not udid:
            QMessageBox.warning(self._shell, "No device", "Select a device UDID.")
            return
        try:
            skip_list = _resolve_skip_panes(self.enroll_preset_combo.currentText(), None)
        except ValueError as exc:
            QMessageBox.warning(self._shell, "Invalid preset", f"Invalid skip preset: {exc}")
            return

        wifi_ssid = self.enroll_wifi_ssid.text().strip() or None
        wifi_password = self.enroll_wifi_password.text() or None
        wifi_encryption = self.enroll_wifi_enc.currentText()

        self._shell._log(f"Starting supervised enrollment for {udid}...")

        def progress(msg: str) -> None:
            masked = sanitize_text(_redact_in_text(msg, wifi_password))
            self._shell._log(f"  {masked}")

        def work() -> Any:
            return _make_supervised(
                cert_path=org.cert_path,
                key_path=org.key_path,
                org_name=org.name,
                org_uuid=org.org_id,
                skip_list=skip_list,
                mdm_url=org.mdm_url,
                mdm_checkin_url=org.checkin_url,
                mdm_topic=org.mdm_topic,
                wifi_ssid=wifi_ssid,
                wifi_password=wifi_password,
                wifi_encryption=wifi_encryption,
                udid=udid,
                progress_callback=progress,
            )

        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(work)
        self._shell._run_worker(worker, self._on_make_supervised_result, [self.make_supervised_btn])

    @Slot(object, object)
    def _on_make_supervised_result(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Enrollment failed: {error}")
            return
        if result is None:
            self._shell._log("Enrollment completed with no result.")
            return
        self._shell._log(f"Enrollment result: supervised={result.supervised}, MDM={result.mdm_enrolled}, WiFi={result.wifi_installed}")
        if result.errors:
            self._shell._log("Errors:")
            for err in result.errors:
                self._shell._log(f"  - {err}")

    def _guided_enroll(self) -> None:
        org = self._resolve_enroll_org()
        if not org:
            return
        if not org.cert_path or not org.key_path:
            QMessageBox.warning(
                self._shell,
                "Missing identity",
                (
                    f"Organization '{org.name}' needs a supervising "
                    "certificate and key. Generate one in the "
                    "Organizations tab first."
                ),
            )
            return
        udid = self.enroll_udid_combo.currentText().strip()
        if not udid:
            QMessageBox.warning(self._shell, "No device", "Select a device UDID.")
            return
        try:
            skip_list = _resolve_skip_panes(
                self.enroll_preset_combo.currentText(), None
            )
        except ValueError as exc:
            QMessageBox.warning(self._shell, "Invalid preset", str(exc))
            return

        wifi_ssid = self.enroll_wifi_ssid.text().strip() or None
        wifi_password = self.enroll_wifi_password.text() or None
        wifi_encryption = self.enroll_wifi_enc.currentText()

        summary = (
            f"Enroll {udid} with '{org.name}'\n\n"
            f"  Preset: {self.enroll_preset_combo.currentText()}\n"
            f"  WiFi: {wifi_ssid or '(none)'}\n"
            f"  MDM URL: {org.mdm_url or '(none)'}\n\n"
            "This will activate, supervise, and install the MDM profile."
        )
        reply = QMessageBox.question(
            self._shell,
            "Confirm Guided Enrollment",
            summary,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._shell._log(f"Guided enrollment starting for {udid}...")

        def progress(msg: str) -> None:
            masked = sanitize_text(_redact_in_text(msg, wifi_password))
            self._shell._log(f"  {masked}")

        def work() -> Any:
            return _make_supervised(
                cert_path=org.cert_path,
                key_path=org.key_path,
                org_name=org.name,
                org_uuid=org.org_id,
                skip_list=skip_list,
                mdm_url=org.mdm_url,
                mdm_checkin_url=org.checkin_url,
                mdm_topic=org.mdm_topic,
                wifi_ssid=wifi_ssid,
                wifi_password=wifi_password,
                wifi_encryption=wifi_encryption,
                udid=udid,
                progress_callback=progress,
            )

        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(work)
        self._shell._run_worker(
            worker,
            self._on_make_supervised_result,
            [self.guided_enroll_btn, self.make_supervised_btn],
        )

    # -- Check Status / Prepare Re-Enroll -------------------------------

    def _check_status(self) -> None:
        udid = self.enroll_udid_combo.currentText().strip()
        if not udid:
            QMessageBox.warning(self._shell, "No device", "Select a device UDID.")
            return
        self._shell._log(f"Checking status for {udid}...")
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(lambda: _get_device_enrollment_state(udid))
        self._shell._run_worker(worker, self._on_status_result, [self.check_status_btn])

    @Slot(object, object)
    def _on_status_result(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Status check failed: {error}")
            return
        state = result
        if not isinstance(state, dict):
            self._shell._log(f"Status check returned unexpected data: {state!r}")
            return
        if "error" in state:
            self._shell._log(f"Could not get state: {state['error']}")
            return
        self._shell._log(f"Activation: {state.get('activation_state', 'Unknown')}")
        self._shell._log(f"Supervised: {state.get('is_supervised', False)}")
        self._shell._log(f"Cloud Config: {state.get('cloud_config_applied', False)}")

    def _prepare_reenroll(self) -> None:
        udid = self.enroll_udid_combo.currentText().strip()
        if not udid:
            QMessageBox.warning(self._shell, "No device", "Select a device UDID.")
            return
        device = next((d for d in self._shell._devices if d.udid == udid), None)
        device_label = f"{device.device_name} ({device.udid})" if device else udid
        reply = QMessageBox.question(
            self._shell,
            "Confirm Re-Enrollment",
            (
                f"Erase cloud configuration on {device_label}?\n\n"
                "This removes the supervised configuration so the device can be "
                "re-enrolled. The device will need to be re-trusted."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._shell._log(f"Preparing {udid} for re-enrollment...")
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(lambda: _erase_device_for_reenrollment(udid))
        self._shell._run_worker(worker, self._on_reenroll_result, [self.prepare_reenroll_btn])

    @Slot(object, object)
    def _on_reenroll_result(self, result: Any, error: Exception | None) -> None:
        if error:
            self._shell._log(f"Re-enrollment preparation failed: {error}")
        else:
            self._shell._log("Device cloud config erased. Ready for fresh enrollment.")