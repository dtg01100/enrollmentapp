"""PySide6 GUI for ios-enroll.

PySide provides Qt's native widgets and thread-safe signals across supported platforms.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from apple_device_cli import __version__
from apple_device_cli.core.redaction import redact_name, sanitize_text
from apple_device_cli.device.connection import (
    ensure_device_pairing,
    get_device_info,
    list_devices,
)
from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.enrollment.activation import activate_device
from apple_device_cli.enrollment.skip_panes import PRESETS, resolve_skip_panes
from apple_device_cli.enrollment.supervised import (
    erase_device_for_reenrollment,
    get_device_enrollment_state,
    make_supervised,
    validate_enrollment_prerequisites,
)
from apple_device_cli.orgs.identity import generate_org_identity
from apple_device_cli.orgs.manager import Organization, OrganizationManager


# PySide6 is an optional dependency (``[gui]`` extra). It is imported lazily
# inside ``_require_pyside6()`` so ``import apple_device_cli.gui_qt`` succeeds
# on a headless install and ``ios-enroll-gui`` / ``python -m
# apple_device_cli.gui_qt`` surface a friendly install hint via the
# ``RuntimeError`` they raise — instead of a top-level ImportError traceback.
if TYPE_CHECKING:
    from PySide6.QtCore import QEvent, QThread, Signal, Slot
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )


# Characters ``OrganizationManager._sanitize_name`` rewrites to ``_``. We reject
# these names up-front so the on-disk directory matches what the user typed.
DISALLOWED_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")
SAFE_TOPIC_CHARS = re.compile(r"^[A-Za-z0-9._\-:]+$")
MIN_IDENTITY_DAYS = 1
MAX_IDENTITY_DAYS = 365 * 100  # ~100 years


class OrgValidationError(ValueError):
    """Raised when user-entered org metadata fails validation."""


def validate_org_fields(
    name: str,
    mdm_url: str | None = None,
    checkin_url: str | None = None,
    mdm_topic: str | None = None,
) -> None:
    """Validate user-entered org metadata.

    Raises ``OrgValidationError`` with a user-facing message on the first
    invalid field. Keeping validation in a free function (instead of inside
    the dialog) means tests can exercise it without spinning up a QDialog.
    """
    if not name:
        raise OrgValidationError("Organization name is required.")
    if DISALLOWED_NAME_CHARS.search(name):
        raise OrgValidationError(
            "Organization name may only contain letters, digits, '.', '-', and '_'."
        )
    if mdm_url and not mdm_url.startswith(("http://", "https://")):
        raise OrgValidationError("MDM URL must start with http:// or https://.")
    if checkin_url and not checkin_url.startswith(("http://", "https://")):
        raise OrgValidationError("Check-in URL must start with http:// or https://.")
    if mdm_topic and not SAFE_TOPIC_CHARS.match(mdm_topic):
        raise OrgValidationError(
            "MDM Topic may only contain letters, digits, '.', '-', '_', and ':'."
        )


def validate_identity_days(days: int) -> int:
    """Validate that ``days`` is in the supported range.

    Returns the value unchanged so callers can use it directly. Raises
    ``ValueError`` with a user-facing message when out of range.
    """
    if not MIN_IDENTITY_DAYS <= days <= MAX_IDENTITY_DAYS:
        raise ValueError(f"Validity must be between {MIN_IDENTITY_DAYS} and {MAX_IDENTITY_DAYS} days.")
    return days


def _redact_in_text(text: str, secret: str | None) -> str:
    """Replace any occurrence of ``secret`` in ``text`` with a mask.

    Used to scrub the WiFi password out of progress messages. ``secret`` is
    only substituted when it is non-empty to avoid turning every whitespace
    run into bullets.
    """
    if not secret or not text:
        return text
    return text.replace(secret, "***")


def _require_pyside6() -> None:
    """Import PySide6 and define the Qt-using classes on this module.

    Called from ``run_gui()`` so the rest of the module loads cleanly when
    PySide6 isn't installed. Raises ``RuntimeError`` with an install hint
    on ``ImportError`` so callers (``_main``, ``cli.py --gui``) can show a
    friendly message instead of a traceback. Idempotent: a second call
    after a successful first call is a no-op.
    """
    # Idempotency guard: once a Qt-using class has been materialized, the
    # names are bound as module globals and PySide6 is importable. Subsequent
    # calls don't need to re-define classes or re-import.
    if "EnrollmentApp" in globals():
        return
    global WorkerThread, StreamingWorkerThread, EnrollmentApp
    global QEvent, QThread, Signal, Slot
    global QApplication
    global QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout
    global QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow
    global QMessageBox, QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget
    try:
        from PySide6.QtCore import QEvent, QThread, Signal, Slot  # noqa: F401
        from PySide6.QtWidgets import (  # noqa: F401
            QApplication,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError(
            "PySide6 is not available. Install with: uv pip install 'ios-enroll[gui]'"
        ) from exc

    class WorkerThread(QThread):
        """Run a blocking operation outside the GUI thread."""

        completed = Signal(object, object)  # result, error

        def __init__(self, fn: Callable[[], Any]) -> None:
            super().__init__()
            self.fn = fn
            self.result = None
            self.error: Exception | None = None

        def run(self) -> None:
            try:
                self.result = self.fn()
            except Exception as exc:  # noqa: BLE001
                self.error = exc
            finally:
                self.completed.emit(self.result, self.error)


    class StreamingWorkerThread(QThread):
        """Run a subprocess and stream stdout to a callback.

        Distinct from ``WorkerThread`` (which wraps a single callable):
        this one launches an external command (e.g. ``idevicerestore``)
        and reads its stdout line by line, invoking ``on_progress`` for
        each line. When the process exits, ``on_finished`` is called
        once with a ``{"returncode": int, "stdout": str, "stderr": str}``
        dict (or an exception, if the launch itself failed).

        The callbacks are wired to the ``progress`` /
        ``finished_with_result`` signals in the constructor, so they are
        delivered on the GUI thread (queued) while the blocking read
        loop runs on this QThread.

        NOT for use with a process the user wants to cancel — the
        restore engine does not support mid-restore cancellation (see
        the restore spec).
        """

        progress = Signal(str)
        finished_with_result = Signal(object, object)  # result, error

        def __init__(
            self,
            cmd: list[str],
            on_progress: Callable[[str], None],
            on_finished: Callable[[Any, Exception | None], None],
            parent=None,
        ) -> None:
            super().__init__(parent)
            self._cmd = cmd
            self._result: dict | None = None
            self._error: Exception | None = None
            self._proc: subprocess.Popen | None = None
            self.progress.connect(on_progress)
            self.finished_with_result.connect(on_finished)

        def run(self) -> None:
            try:
                self._proc = subprocess.Popen(
                    self._cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                stdout_lines: list[str] = []
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    stripped = line.rstrip("\n")
                    stdout_lines.append(line)
                    self.progress.emit(stripped)
                self._proc.wait()
                self._result = {
                    "returncode": self._proc.returncode,
                    "stdout": "".join(stdout_lines),
                    "stderr": "",
                }
            except Exception as exc:  # noqa: BLE001
                self._error = exc
            finally:
                self.finished_with_result.emit(self._result, self._error)


    class EnrollmentApp(QMainWindow):
        """Main PySide6 application window."""

        log_signal = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"ios-enroll {__version__}")
            self.setGeometry(100, 100, 900, 700)

            self._devices: list[DeviceInfo] = []
            self._orgs: list[Organization] = []
            self._workers: list[QThread] = []
            self._request_token: int = 0

            self._setup_ui()
            self.log_signal.connect(self._append_log)
            self.enroll_org_combo.currentIndexChanged.connect(self._on_enroll_org_changed)
            self._log("GUI initialized. Connect an iOS device to begin.")
            self._load_initial_state()

        def _setup_ui(self) -> None:
            central_widget = QWidget()
            self.setCentralWidget(central_widget)
            layout = QVBoxLayout(central_widget)

            header = QLabel("iOS Supervised Enrollment")
            header.setStyleSheet("font-size: 16px; font-weight: bold;")
            layout.addWidget(header)

            self.tabs = QTabWidget()
            layout.addWidget(self.tabs)

            self.devices_tab = self._create_devices_tab()
            self.orgs_tab = self._create_orgs_tab()
            self.enroll_tab = self._create_enroll_tab()

            self.tabs.addTab(self.devices_tab, "Devices")
            self.tabs.addTab(self.orgs_tab, "Organizations")
            self.tabs.addTab(self.enroll_tab, "Enrollment")

            log_group = QWidget()
            log_layout = QVBoxLayout(log_group)
            log_layout.addWidget(QLabel("Log:"))
            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            log_layout.addWidget(self.log_text)
            layout.addWidget(log_group)

        def _create_devices_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)

            toolbar = QHBoxLayout()
            self.refresh_devices_btn = QPushButton("Refresh Devices")
            self.refresh_devices_btn.clicked.connect(self._refresh_devices)
            toolbar.addWidget(self.refresh_devices_btn)

            self.device_info_btn = QPushButton("Show Device Info")
            self.device_info_btn.clicked.connect(self._show_device_info)
            toolbar.addWidget(self.device_info_btn)

            self.activate_btn = QPushButton("Activate")
            self.activate_btn.clicked.connect(self._activate_device)
            toolbar.addWidget(self.activate_btn)

            self.pair_btn = QPushButton("Pair/Trust")
            self.pair_btn.clicked.connect(self._pair_device)
            toolbar.addWidget(self.pair_btn)

            toolbar.addStretch()
            layout.addLayout(toolbar)

            self.devices_list = QListWidget()
            layout.addWidget(self.devices_list)

            return widget

        def _create_orgs_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)

            toolbar = QHBoxLayout()
            self.refresh_orgs_btn = QPushButton("Refresh Orgs")
            self.refresh_orgs_btn.clicked.connect(self._refresh_orgs)
            toolbar.addWidget(self.refresh_orgs_btn)

            self.create_org_btn = QPushButton("Create Org")
            self.create_org_btn.clicked.connect(self._create_org_dialog)
            toolbar.addWidget(self.create_org_btn)

            self.generate_id_btn = QPushButton("Generate Identity")
            self.generate_id_btn.clicked.connect(self._generate_identity_dialog)
            toolbar.addWidget(self.generate_id_btn)

            self.delete_org_btn = QPushButton("Delete Org")
            self.delete_org_btn.clicked.connect(self._delete_org)
            toolbar.addWidget(self.delete_org_btn)

            toolbar.addStretch()
            layout.addLayout(toolbar)

            self.orgs_list = QListWidget()
            layout.addWidget(self.orgs_list)

            return widget

        def _create_enroll_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)

            form_layout = QFormLayout()

            self.enroll_org_combo = QComboBox()
            form_layout.addRow("Organization:", self.enroll_org_combo)

            self.enroll_preset_combo = QComboBox()
            self.enroll_preset_combo.addItems(list(PRESETS.keys()))
            self.enroll_preset_combo.setCurrentText("standard")
            form_layout.addRow("Skip preset:", self.enroll_preset_combo)

            self.enroll_wifi_ssid = QLineEdit()
            form_layout.addRow("WiFi SSID:", self.enroll_wifi_ssid)

            self.enroll_wifi_password = QLineEdit()
            self.enroll_wifi_password.setEchoMode(QLineEdit.EchoMode.Password)
            form_layout.addRow("WiFi Password:", self.enroll_wifi_password)

            self.enroll_wifi_enc = QComboBox()
            self.enroll_wifi_enc.addItems(["WPA", "WEP", "None"])
            self.enroll_wifi_enc.setCurrentText("WPA")
            form_layout.addRow("WiFi Encryption:", self.enroll_wifi_enc)

            self.enroll_udid_combo = QComboBox()
            form_layout.addRow("Device UDID:", self.enroll_udid_combo)

            use_device_btn = QPushButton("Use Selected Device")
            use_device_btn.clicked.connect(self._use_selected_device)
            form_layout.addRow(use_device_btn)

            layout.addLayout(form_layout)

            buttons_layout = QHBoxLayout()
            self.validate_btn = QPushButton("Validate Prerequisites")
            self.validate_btn.clicked.connect(self._validate_prereqs)
            buttons_layout.addWidget(self.validate_btn)

            self.make_supervised_btn = QPushButton("Make Supervised")
            self.make_supervised_btn.clicked.connect(self._make_supervised)
            buttons_layout.addWidget(self.make_supervised_btn)

            self.check_status_btn = QPushButton("Check Status")
            self.check_status_btn.clicked.connect(self._check_status)
            buttons_layout.addWidget(self.check_status_btn)

            self.prepare_reenroll_btn = QPushButton("Prepare Re-Enrollment")
            self.prepare_reenroll_btn.clicked.connect(self._prepare_reenroll)
            buttons_layout.addWidget(self.prepare_reenroll_btn)

            buttons_layout.addStretch()
            layout.addLayout(buttons_layout)

            return widget

        def _log(self, message: str) -> None:
            self.log_signal.emit(message)

        def _append_log(self, message: str) -> None:
            self.log_text.append(message)

        def _load_initial_state(self) -> None:
            self._refresh_devices()
            self._refresh_orgs()

        def _selected_device(self) -> DeviceInfo | None:
            current = self.devices_list.currentRow()
            if current < 0 or current >= len(self._devices):
                return None
            return self._devices[current]

        def _selected_org(self) -> Organization | None:
            current = self.orgs_list.currentRow()
            if current < 0 or current >= len(self._orgs):
                return None
            return self._orgs[current]

        def _run_worker(
            self,
            worker: WorkerThread,
            on_finished: Callable,
            buttons_to_disable: Iterable[QPushButton] = (),
            token: int | None = None,
        ) -> None:
            """Start a worker, wire completion, and gate UI buttons while it runs.

            Each button passed in ``buttons_to_disable`` is disabled until the
            worker emits ``completed``. This prevents the user from re-issuing a
            slow action (e.g. clicking "Make Supervised" twice) and racing two
            blocking USB calls against the same device. When ``token`` is given,
            the slot is invoked as ``on_finished(result, error, token)`` so the
            handler can detect stale completions.
            """
            buttons = list(buttons_to_disable)
            for btn in buttons:
                btn.setEnabled(False)

            if token is None:
                @Slot(object, object)
                def _completed(result: Any, error: Exception | None) -> None:
                    try:
                        on_finished(result, error)
                    finally:
                        for btn in buttons:
                            btn.setEnabled(True)

                worker.completed.connect(_completed)
            else:
                @Slot(object, object)
                def _completed_token(result: Any, error: Exception | None) -> None:
                    try:
                        on_finished(result, error, token)
                    finally:
                        for btn in buttons:
                            btn.setEnabled(True)

                worker.completed.connect(_completed_token)

            def remove_worker() -> None:
                if worker in self._workers:
                    self._workers.remove(worker)

            worker.finished.connect(remove_worker)
            self._workers.append(worker)
            worker.start()

        def _refresh_devices(self) -> None:
            self._log("Refreshing device list...")
            token = self._next_token()
            worker = WorkerThread(list_devices)
            self._run_worker(worker, self._on_devices_refreshed, [self.refresh_devices_btn], token=token)

        @Slot(object, object)
        def _on_devices_refreshed(self, result: Any, error: Exception | None, token: int) -> None:
            if not self._is_current_token(token):
                return  # stale completion
            if error:
                self._log(f"Failed to list devices: {error}")
                return
            devices = result or []
            self._devices = list(devices)
            self.devices_list.clear()
            for device in self._devices:
                display = f"{device.device_name}  ({device.udid})"
                QListWidgetItem(display, self.devices_list)
            if self._devices:
                self.devices_list.setCurrentRow(0)
            self._update_enroll_udids()
            self._log(f"Found {len(self._devices)} device(s).")

        def _next_token(self) -> int:
            self._request_token += 1
            return self._request_token

        def _is_current_token(self, token: int) -> bool:
            return token == self._request_token

        def _update_enroll_udids(self) -> None:
            self.enroll_udid_combo.clear()
            self.enroll_udid_combo.addItems([d.udid for d in self._devices])
            if self._devices:
                self.enroll_udid_combo.setCurrentIndex(0)

        def _use_selected_device(self) -> None:
            device = self._selected_device()
            if device:
                self.enroll_udid_combo.setCurrentText(device.udid)
            else:
                QMessageBox.warning(self, "No device", "Select a device in the Devices tab first.")
                self._log("No device selected in the Devices tab.")

        def _show_device_info(self) -> None:
            device = self._selected_device()
            if not device:
                QMessageBox.warning(self, "No device", "Select a device first.")
                return
            self._log(f"Fetching info for {device.device_name}...")
            udid = device.udid
            worker = WorkerThread(lambda: get_device_info(udid))
            self._run_worker(worker, self._on_device_info, [self.device_info_btn])

        @Slot(object, object)
        def _on_device_info(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Failed to get device info: {error}")
                return
            info = result
            if not info:
                self._log("Device info unavailable.")
                return
            self._log(f"UDID: {info.udid}")
            self._log(f"Name: {info.device_name}")
            self._log(f"Type: {info.device_type}")
            self._log(f"iOS: {info.firmware_version} ({info.build_version})")
            if info.ecid:
                self._log(f"ECID: {info.ecid}")

        def _activate_device(self) -> None:
            device = self._selected_device()
            if not device:
                QMessageBox.warning(self, "No device", "Select a device first.")
                return
            self._log(f"Activating {device.device_name}...")
            udid = device.udid
            worker = WorkerThread(lambda: activate_device(udid))
            self._run_worker(worker, self._on_activation_result, [self.activate_btn])

        @Slot(object, object)
        def _on_activation_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Activation failed: {error}")
            else:
                self._log("Activation completed.")

        def _pair_device(self) -> None:
            device = self._selected_device()
            if not device:
                QMessageBox.warning(self, "No device", "Select a device first.")
                return
            self._log(f"Ensuring pairing with {device.device_name}...")
            udid = device.udid
            worker = WorkerThread(lambda: ensure_device_pairing(udid))
            self._run_worker(worker, self._on_pair_result, [self.pair_btn])

        @Slot(object, object)
        def _on_pair_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Pairing failed: {error}")
            else:
                self._log("Device paired/trusted successfully.")

        def _refresh_orgs(self) -> None:
            self._log("Refreshing organizations...")
            token = self._next_token()
            worker = WorkerThread(lambda: OrganizationManager().list_orgs())
            self._run_worker(worker, self._on_orgs_refreshed, [self.refresh_orgs_btn], token=token)

        @Slot(object, object)
        def _on_orgs_refreshed(self, result: Any, error: Exception | None, token: int) -> None:
            if not self._is_current_token(token):
                return
            if error:
                self._log(f"Failed to list organizations: {error}")
                return
            orgs = result or []
            self._orgs = list(orgs)
            self.orgs_list.clear()
            for org in self._orgs:
                has_identity = "yes" if org.cert_path and org.key_path else "no"
                display = f"{org.name}  (MDM: {org.mdm_url or 'none'}, identity: {has_identity})"
                QListWidgetItem(display, self.orgs_list)
            if self._orgs:
                self.orgs_list.setCurrentRow(0)
            self._update_enroll_orgs()
            self._log(f"Found {len(self._orgs)} organization(s).")

        def _update_enroll_orgs(self) -> None:
            self.enroll_org_combo.clear()
            self.enroll_org_combo.addItems([o.name for o in self._orgs])
            if self._orgs:
                self.enroll_org_combo.setCurrentIndex(0)

        @Slot(int)
        def _on_enroll_org_changed(self, index: int) -> None:
            """Auto-populate the Enrollment tab WiFi widgets from the selected org's
            wifi.mobileconfig when one is available.
            """
            if index < 0:
                return
            name = self.enroll_org_combo.currentText().strip()
            if not name:
                return

            # Default to empty/reset; only filled in if the org's wifi.mobileconfig
            # has a usable com.apple.wifi.managed payload.
            ssid = ""
            pwd = ""
            enc = "WPA"

            try:
                org = OrganizationManager().get_org(name)
            except Exception as exc:  # noqa: BLE001
                self._log(f"Could not load org '{name}': {exc}")
                org = None

            if org is not None:
                wifi_path = getattr(org, "wifi_config_path", None)
                if not wifi_path:
                    self._log(f"Org '{name}' has no bundled wifi.mobileconfig — fields cleared.")
                else:
                    try:
                        parsed = OrganizationManager().read_wifi_profile(name)
                    except Exception as exc:  # noqa: BLE001
                        self._log(f"Could not read WiFi profile for '{name}': {exc}")
                    else:
                        if parsed:
                            ssid = parsed.get("ssid") or ""
                            pwd = parsed.get("password") or ""
                            enc_raw = parsed.get("encryption") or "WPA"
                            enc = enc_raw if enc_raw in ("WPA", "WEP", "None") else "WPA"
                            self._log(
                                f"Populated WiFi fields from '{name}' "
                                f"(SSID: {redact_name(ssid)}, encryption: {enc})"
                            )
                        else:
                            self._log(
                                f"Org '{name}' has wifi.mobileconfig but no "
                                f"com.apple.wifi.managed payload — fields cleared."
                            )

            self.enroll_wifi_ssid.setText(ssid)
            self.enroll_wifi_password.setText(pwd)
            self.enroll_wifi_enc.setCurrentText(enc)

        def _create_org_dialog(self) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Create Organization")
            dialog.setModal(True)
            layout = QFormLayout(dialog)

            name_edit = QLineEdit()
            org_id_edit = QLineEdit()
            mdm_url_edit = QLineEdit()
            checkin_url_edit = QLineEdit()
            mdm_topic_edit = QLineEdit()

            layout.addRow("Name:", name_edit)
            layout.addRow("Org ID:", org_id_edit)
            layout.addRow("MDM URL:", mdm_url_edit)
            layout.addRow("Check-in URL:", checkin_url_edit)
            layout.addRow("MDM Topic:", mdm_topic_edit)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            layout.addRow(button_box)

            def save_org() -> None:
                name = name_edit.text().strip()
                mdm_url = mdm_url_edit.text().strip() or None
                checkin_url = checkin_url_edit.text().strip() or None
                mdm_topic = mdm_topic_edit.text().strip() or None
                try:
                    validate_org_fields(name, mdm_url, checkin_url, mdm_topic)
                except OrgValidationError as exc:
                    QMessageBox.warning(dialog, "Invalid input", str(exc))
                    return

                org = Organization(
                    name=name,
                    org_id=org_id_edit.text().strip() or None,
                    mdm_url=mdm_url,
                    checkin_url=checkin_url,
                    mdm_topic=mdm_topic,
                )
                try:
                    OrganizationManager().save_org(org)
                    self._log(f"Created organization: {name}")
                    dialog.accept()
                    self._refresh_orgs()
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(dialog, "Save failed", f"Failed to create organization: {exc}")
                    self._log(f"Failed to create organization: {exc}")

            button_box.accepted.connect(save_org)
            button_box.rejected.connect(dialog.reject)
            dialog.exec()

        def _generate_identity_dialog(self) -> None:
            org = self._selected_org()
            if not org:
                QMessageBox.warning(self, "No organization", "Select an organization first.")
                return

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Generate Identity for {org.name}")
            dialog.setModal(True)
            layout = QFormLayout(dialog)

            days_edit = QLineEdit(str(365 * 5))
            layout.addRow("Validity (days):", days_edit)

            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            layout.addRow(button_box)

            def generate_identity() -> None:
                try:
                    days = int(days_edit.text().strip())
                except ValueError:
                    QMessageBox.warning(dialog, "Invalid value", "Validity must be a whole number of days.")
                    return
                try:
                    validate_identity_days(days)
                except ValueError as exc:
                    QMessageBox.warning(dialog, "Invalid value", str(exc))
                    return

                def work() -> tuple[bytes, bytes]:
                    return generate_org_identity(org.name, days)

                def on_done(result: Any, error: Exception | None) -> None:
                    self._on_identity_generated(dialog, org, result, error)

                worker = WorkerThread(work)
                self._run_worker(worker, on_done, [self.generate_id_btn])

            button_box.accepted.connect(generate_identity)
            button_box.rejected.connect(dialog.reject)
            dialog.exec()

        @Slot(object, object)
        def _on_identity_generated(
            self,
            dialog: QDialog,
            org: Organization,
            result: Any,
            error: Exception | None,
        ) -> None:
            if error:
                self._log(f"Identity generation failed: {error}")
                QMessageBox.warning(dialog, "Generation failed", f"Identity generation failed: {error}")
                return
            if not result or not isinstance(result, tuple) or len(result) != 2:
                self._log("Identity generation returned an unexpected result.")
                return
            cert_der, key_der = result
            manager = OrganizationManager()
            org_dir = manager.org_dir_for(org.name)
            try:
                _write_identity_atomic(org_dir, cert_der, key_der)
            except OSError as exc:
                self._log(f"Failed to write identity: {exc}")
                QMessageBox.warning(dialog, "Write failed", f"Failed to write identity: {exc}")
                return
            org.cert_path = str(org_dir / "cert.der")
            org.key_path = str(org_dir / "key.der")
            manager.save_org(org, overwrite=True)
            self._log(f"Generated identity for {org.name}")
            dialog.accept()
            self._refresh_orgs()

        def _delete_org(self) -> None:
            org = self._selected_org()
            if not org:
                QMessageBox.warning(self, "No organization", "Select an organization first.")
                return
            has_identity = bool(org.cert_path and org.key_path)
            warning = (
                f"Delete organization '{org.name}'?\n\n"
                "This permanently removes the org and any attached MDM profile."
            )
            if has_identity:
                warning += (
                    "\n\nWARNING: The supervising certificate and private key "
                    "stored in this org will be permanently lost. Devices already "
                    "enrolled with this org will not be able to re-enroll without "
                    "first exporting the org."
                )
            reply = QMessageBox.question(
                self,
                "Confirm Delete",
                warning,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                OrganizationManager().delete_org(org.name)
                self._log(f"Deleted organization: {org.name}")
                self._refresh_orgs()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Delete failed", f"Failed to delete organization: {exc}")
                self._log(f"Failed to delete organization: {exc}")

        def _resolve_enroll_org(self) -> Organization | None:
            name = self.enroll_org_combo.currentText().strip()
            if not name:
                QMessageBox.warning(self, "No organization", "Select an organization.")
                return None
            org = OrganizationManager().get_org(name)
            if not org:
                QMessageBox.warning(self, "Unknown organization", f"Organization not found: {name}")
                return None
            return org

        def _validate_prereqs(self) -> None:
            org = self._resolve_enroll_org()
            if not org:
                return
            self._log(f"Validating prerequisites for {org.name}...")

            def work() -> list[str]:
                return validate_enrollment_prerequisites(
                    cert_path=org.cert_path,
                    key_path=org.key_path,
                    org_name=org.name,
                    mdm_url=org.mdm_url,
                    check_mdm_reachability=False,
                )

            worker = WorkerThread(work)
            self._run_worker(worker, self._on_validation_result, [self.validate_btn])

        @Slot(object, object)
        def _on_validation_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Validation failed: {error}")
                return
            errors = result or []
            if errors:
                self._log("Validation failed:")
                for err in errors:
                    self._log(f"  - {err}")
            else:
                self._log("All prerequisites valid.")

        def _make_supervised(self) -> None:
            org = self._resolve_enroll_org()
            if not org:
                return
            if not org.cert_path or not org.key_path:
                QMessageBox.warning(
                    self,
                    "Missing identity",
                    f"Organization '{org.name}' needs a supervising certificate and key. "
                    "Generate one in the Organizations tab first.",
                )
                return
            udid = self.enroll_udid_combo.currentText().strip()
            if not udid:
                QMessageBox.warning(self, "No device", "Select a device UDID.")
                return

            try:
                skip_list = resolve_skip_panes(self.enroll_preset_combo.currentText(), None)
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid preset", f"Invalid skip preset: {exc}")
                return

            wifi_ssid = self.enroll_wifi_ssid.text().strip() or None
            wifi_password = self.enroll_wifi_password.text() or None  # keep; never log
            wifi_encryption = self.enroll_wifi_enc.currentText()

            self._log(f"Starting supervised enrollment for {udid}...")

            def progress(msg: str) -> None:
                # Scrub the WiFi password from any progress message before logging.
                masked = sanitize_text(_redact_in_text(msg, wifi_password))
                self._log(f"  {masked}")

            def work() -> Any:
                return make_supervised(
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

            worker = WorkerThread(work)
            self._run_worker(worker, self._on_make_supervised_result, [self.make_supervised_btn])

        @Slot(object, object)
        def _on_make_supervised_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Enrollment failed: {error}")
                return
            if result is None:
                self._log("Enrollment completed with no result.")
                return
            self._log(f"Enrollment result: supervised={result.supervised}, MDM={result.mdm_enrolled}, WiFi={result.wifi_installed}")
            if result.errors:
                self._log("Errors:")
                for err in result.errors:
                    self._log(f"  - {err}")

        def _check_status(self) -> None:
            udid = self.enroll_udid_combo.currentText().strip()
            if not udid:
                QMessageBox.warning(self, "No device", "Select a device UDID.")
                return
            self._log(f"Checking status for {udid}...")
            worker = WorkerThread(lambda: get_device_enrollment_state(udid))
            self._run_worker(worker, self._on_status_result, [self.check_status_btn])

        @Slot(object, object)
        def _on_status_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Status check failed: {error}")
                return
            state = result
            if not isinstance(state, dict):
                self._log(f"Status check returned unexpected data: {state!r}")
                return
            if "error" in state:
                self._log(f"Could not get state: {state['error']}")
                return
            self._log(f"Activation: {state.get('activation_state', 'Unknown')}")
            self._log(f"Supervised: {state.get('is_supervised', False)}")
            self._log(f"Cloud Config: {state.get('cloud_config_applied', False)}")

        def _prepare_reenroll(self) -> None:
            udid = self.enroll_udid_combo.currentText().strip()
            if not udid:
                QMessageBox.warning(self, "No device", "Select a device UDID.")
                return
            device = next((d for d in self._devices if d.udid == udid), None)
            device_label = f"{device.device_name} ({device.udid})" if device else udid
            reply = QMessageBox.question(
                self,
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
            self._log(f"Preparing {udid} for re-enrollment...")
            worker = WorkerThread(lambda: erase_device_for_reenrollment(udid))
            self._run_worker(worker, self._on_reenroll_result, [self.prepare_reenroll_btn])

        @Slot(object, object)
        def _on_reenroll_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log(f"Re-enrollment preparation failed: {error}")
            else:
                self._log("Device cloud config erased. Ready for fresh enrollment.")

        def closeEvent(self, event: QEvent) -> None:
            """Refuse close while workers are still running.

            Blocking USB calls running inside ``WorkerThread.run`` cannot be
            cancelled by ``quit()`` — closing the window mid-enrollment would race
            the Python interpreter against in-flight C-level USB IO and can leave
            the device in an undefined state. Users must wait for in-flight work
            to finish (or let it fail naturally) before the window can close.
            """
            if self._workers:
                busy = len(self._workers)
                QMessageBox.warning(
                    self,
                    "Operations in progress",
                    f"{busy} operation(s) are still running. Wait for them to "
                    "finish before closing the window.",
                )
                event.ignore()
                return
            event.accept()


def _write_identity_atomic(org_dir: Path, cert_der: bytes, key_der: bytes) -> None:
    """Write cert+key to ``org_dir`` atomically.

    Writes each file to a sibling ``.tmp`` first and then ``os.replace``s into
    place. Either both files end up on disk or neither does — a partial write
    can never leave the org in a half-configured state.
    """
    import tempfile

    org_dir = Path(org_dir)
    org_dir.mkdir(parents=True, exist_ok=True)

    cert_final = org_dir / "cert.der"
    key_final = org_dir / "key.der"
    cert_tmp: Path | None = None
    key_tmp: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=org_dir,
            prefix=".cert.",
            suffix=".tmp",
            delete=False,
        ) as f:
            f.write(cert_der)
            cert_tmp = Path(f.name)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=org_dir,
            prefix=".key.",
            suffix=".tmp",
            delete=False,
        ) as f:
            f.write(key_der)
            key_tmp = Path(f.name)
        os.replace(cert_tmp, cert_final)
        os.replace(key_tmp, key_final)
    except Exception:
        for tmp in (cert_tmp, key_tmp):
            if tmp is None:
                continue
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        raise


def run_gui() -> None:
    """Launch the PySide6 GUI.

    Imports PySide6 lazily via ``_require_pyside6()`` (which also defines
    ``WorkerThread`` and ``EnrollmentApp`` if they haven't been materialized
    yet). Raises ``RuntimeError`` with an install hint when PySide6 isn't
    installed — callers (``_main``, ``cli.py --gui``) catch that and show
    a friendly message instead of a traceback.
    """
    _require_pyside6()
    app = QApplication.instance() or QApplication(sys.argv)
    window = EnrollmentApp()
    window.show()
    app.exec()


# Names that require PySide6 to be installed. Defined here (vs. imported
# at module top-level) so ``import apple_device_cli.gui_qt`` succeeds on a
# headless install. ``__getattr__`` materializes them on first access and
# raises the friendly ``RuntimeError`` if PySide6 is missing.
_LAZY_QT_NAMES = frozenset({"WorkerThread", "StreamingWorkerThread", "EnrollmentApp", "run_gui"})


def __getattr__(name: str) -> Any:
    """PEP 562: lazily materialize Qt-using names on first attribute access.

    Triggers ``_require_pyside6()`` (which imports PySide6 and defines the
    Qt-using classes) when ``WorkerThread``, ``EnrollmentApp``, or
    ``run_gui`` is first accessed. This keeps ``from apple_device_cli.gui_qt
    import EnrollmentApp`` working while still allowing the module to
    import on a core-only install.
    """
    if name in _LAZY_QT_NAMES:
        _require_pyside6()
        value = globals().get(name)
        if value is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _main() -> int:
    """Entry point for ``python -m apple_device_cli.gui_qt`` and the
    ``ios-enroll-gui`` console script.

    Returns an exit code instead of letting an unhandled ``RuntimeError``
    traceback reach the user, so missing PySide6 surfaces as a single-line
    install hint on stderr.
    """
    try:
        run_gui()
    except RuntimeError as exc:
        # ``run_gui()`` raises RuntimeError when PySide6 isn't installed.
        # Show the helpful install hint and exit non-zero — no traceback.
        sys.stderr.write(f"ios-enroll-gui: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
