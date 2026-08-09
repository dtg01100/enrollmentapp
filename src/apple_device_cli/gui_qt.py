"""PySide6 GUI for ios-enroll.

PySide provides Qt's native widgets and thread-safe signals across supported platforms.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
from apple_device_cli.cli_actions import (
    OrgNotFoundError,
    WifiConfigInvalidError,
    WifiConfigNotFoundError,
    set_org_wifi,
)
from apple_device_cli.orgs.identity import generate_org_identity
from apple_device_cli.orgs.manager import Organization, OrganizationManager
from apple_device_cli.restore.cache import (
    cache_state,
    resolve_cache_dir,
    write_cache_config,
)
from apple_device_cli.restore.engine import (
    ProgressEvent,
    VerifyResult,
    _device_ecid,
    _filename_from_url,
    cached_ipsw_path,
    detect_device_mode,
    detect_recovery_devices_present,
    download_ipsw,
    enter_recovery_mode,
    exit_recovery_mode,
    get_product_type_for_udid,
    list_signed_versions,
    parse_ipsw_filename,
    parse_ipsw_url,
    recovery_device_descriptor,
    restore_device as engine_restore_device,
    verify_ipsw,
)


# PySide6 is an optional dependency (``[gui]`` extra). It is imported lazily
# inside ``_require_pyside6()`` so ``import apple_device_cli.gui_qt`` succeeds
# on a headless install and ``ios-enroll-gui`` / ``python -m
# apple_device_cli.gui_qt`` surface a friendly install hint via the
# ``RuntimeError`` they raise — instead of a top-level ImportError traceback.
if TYPE_CHECKING:
    from PySide6.QtCore import QEvent, Qt, QThread, Signal, Slot  # noqa: F401
    from PySide6.QtWidgets import (  # noqa: F401
        QApplication,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QProgressBar,
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


def _cert_expiry(cert_path: str | None) -> datetime | None:
    """Return the cert's ``not_valid_after_utc``, or None if unreadable.

    Returns None for missing files, unparseable bytes, or any error —
    never raises. The Orgs list render path uses this to color-code
    upcoming expiry, and crashing the list because one cert is corrupt
    would be worse than showing "unknown".
    """
    if not cert_path:
        return None
    try:
        path = Path(cert_path)
        if not path.is_file():
            return None
        from cryptography.x509 import load_der_x509_certificate

        with open(path, "rb") as f:
            cert = load_der_x509_certificate(f.read())
        # cryptography >= 42 has not_valid_after_utc; fall back for older.
        return getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after
    except Exception:  # noqa: BLE001
        return None


def _format_cert_expiry_badge(expiry: datetime | None) -> str:
    """Render a small color-indicator suffix for the org list.

    Returns one of: grey dot (no cert), red "expired", yellow " <30d",
    or green dot (healthy). Returns empty string for healthy so the
    list stays uncluttered — color only when something needs attention.
    """
    if expiry is None:
        return " ⚪"
    # Treat naive datetimes as UTC (older cryptography returns naive).
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if expiry < now:
        return " 🔴 expired"
    if expiry < now + timedelta(days=30):
        return " 🟡 <30d"
    return " 🟢"


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
    global WorkerThread, EnrollmentApp
    global QEvent, Qt, QThread, Signal, Slot
    global QApplication
    global QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout
    global QHBoxLayout
    global QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow
    global QMessageBox, QProgressBar, QPushButton, QSplitter, QStatusBar, QTabWidget, QTextEdit
    global QVBoxLayout, QWidget
    global QGroupBox, QSettings
    try:
        from PySide6.QtCore import QEvent, QSettings, Qt, QThread, Signal, Slot  # noqa: F401
        from PySide6.QtWidgets import (  # noqa: F401
            QApplication,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFileDialog,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QSplitter,
            QStatusBar,
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


    class EnrollmentApp(QMainWindow):
        """Main PySide6 application window."""

        log_signal = Signal(str)
        restore_log_signal = Signal(str)
        restore_progress_signal = Signal(object)  # ProgressEvent

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(f"ios-enroll {__version__}")
            self.setGeometry(100, 100, 900, 700)

            self._devices: list[DeviceInfo] = []
            self._orgs: list[Organization] = []
            self._workers: list[QThread] = []
            self._request_token: int = 0
            self._restore_ipsw_path: Path | None = None
            self._restore_selected_udid: str | None = None
            self._restore_step_label: str | None = None
            self._restore_last_percent: int = 0
            self._restore_is_recovery: bool = False

            self._setup_ui()
            self.setStatusBar(QStatusBar())
            self.log_signal.connect(self._append_log)
            self.restore_log_signal.connect(self._append_restore_log)
            self.restore_progress_signal.connect(self._on_restore_progress_event)
            self.enroll_org_combo.currentIndexChanged.connect(self._on_enroll_org_changed)
            self._log("GUI initialized. Connect an iOS device to begin.")
            self._restore_geometry()
            self._load_initial_state()
            self._update_status_bar()

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
            self.restore_tab = self._create_restore_tab()

            self.tabs.addTab(self.devices_tab, "Devices")
            self.tabs.addTab(self.orgs_tab, "Organizations")
            self.tabs.addTab(self.enroll_tab, "Enrollment")
            self.tabs.addTab(self.restore_tab, "Restore")

            log_group = QWidget()
            log_layout = QVBoxLayout(log_group)
            log_layout.setContentsMargins(8, 0, 8, 4)
            log_layout.setSpacing(2)

            log_header = QLabel("Log")
            log_header.setStyleSheet("color: palette(mid); font-weight: 600;")
            log_layout.addWidget(log_header)

            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setObjectName("log_text")
            # Cap the log at a fixed height so a verbose operation can't
            # squeeze the tab content to zero height. Min keeps at least
            # 4 lines visible.
            self.log_text.setMaximumHeight(180)
            self.log_text.setMinimumHeight(80)
            log_layout.addWidget(self.log_text)
            layout.addWidget(log_group)

        def _create_devices_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)

            toolbar = QHBoxLayout()
            toolbar.setSpacing(6)
            self.refresh_devices_btn = QPushButton("Refresh Devices")
            self.refresh_devices_btn.clicked.connect(self._refresh_devices)
            toolbar.addWidget(self.refresh_devices_btn)

            self.device_info_btn = QPushButton("Show Device Info")
            self.device_info_btn.clicked.connect(self._show_device_info)
            toolbar.addWidget(self.device_info_btn)

            self.activate_btn = QPushButton("Activate")
            self.activate_btn.clicked.connect(self._activate_device)
            toolbar.addWidget(self.activate_btn)

            self.pair_btn = QPushButton("Pair / Trust")
            self.pair_btn.clicked.connect(self._pair_device)
            toolbar.addWidget(self.pair_btn)

            toolbar.addStretch()
            layout.addLayout(toolbar)

            # List takes the remaining vertical space; placeholder shown
            # below it when the list is empty (so resizing the list
            # doesn't require keeping the placeholder geometry in sync).
            self.devices_list = QListWidget()
            layout.addWidget(self.devices_list, 1)

            self.devices_empty_label = QLabel(
                "No devices found. Connect an iOS device over USB and "
                "click Refresh Devices."
            )
            self.devices_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.devices_empty_label.setStyleSheet(
                "color: palette(mid); font-size: 13px; padding: 4px;"
            )
            layout.addWidget(self.devices_empty_label)
            self.devices_empty_label.setVisible(self.devices_list.count() == 0)

            return widget

        def _create_orgs_tab(self) -> QWidget:
            widget = QWidget()
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)

            toolbar = QHBoxLayout()
            toolbar.setSpacing(6)
            self.refresh_orgs_btn = QPushButton("Refresh Orgs")
            self.refresh_orgs_btn.clicked.connect(self._refresh_orgs)
            toolbar.addWidget(self.refresh_orgs_btn)

            self.create_org_btn = QPushButton("Create Org")
            self.create_org_btn.clicked.connect(self._create_org_dialog)
            toolbar.addWidget(self.create_org_btn)

            self.generate_id_btn = QPushButton("Generate Identity")
            self.generate_id_btn.clicked.connect(self._generate_identity_dialog)
            toolbar.addWidget(self.generate_id_btn)

            self.edit_org_btn = QPushButton("Edit Org")
            self.edit_org_btn.clicked.connect(self._edit_org)
            toolbar.addWidget(self.edit_org_btn)

            self.import_org_btn = QPushButton("Import…")
            self.import_org_btn.clicked.connect(self._import_org)
            toolbar.addWidget(self.import_org_btn)

            self.export_org_btn = QPushButton("Export…")
            self.export_org_btn.clicked.connect(self._export_org)
            toolbar.addWidget(self.export_org_btn)

            self.attach_wifi_btn = QPushButton("Attach WiFi…")
            self.attach_wifi_btn.clicked.connect(self._attach_wifi)
            toolbar.addWidget(self.attach_wifi_btn)

            self.delete_org_btn = QPushButton("Delete Org")
            self.delete_org_btn.clicked.connect(self._delete_org)
            toolbar.addWidget(self.delete_org_btn)

            toolbar.addStretch()
            layout.addLayout(toolbar)

            self.orgs_list = QListWidget()
            layout.addWidget(self.orgs_list, 1)

            # Read-only details pane; updated by _update_org_details when
            # the user selects a different org in the list above.
            self.orgs_details_label = QLabel("(no organization selected)")
            self.orgs_details_label.setWordWrap(True)
            self.orgs_details_label.setStyleSheet(
                "color: palette(mid); font-size: 12px; padding: 6px;"
                "border: 1px solid palette(midlight); border-radius: 3px;"
            )
            self.orgs_details_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.orgs_details_label.setMinimumHeight(80)
            layout.addWidget(self.orgs_details_label)
            self.orgs_list.currentRowChanged.connect(self._update_org_details)

            self.orgs_empty_label = QLabel(
                "No organizations yet. Click Create Org or Refresh Orgs."
            )
            self.orgs_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.orgs_empty_label.setStyleSheet(
                "color: palette(mid); font-size: 13px; padding: 4px;"
            )
            layout.addWidget(self.orgs_empty_label)
            self.orgs_empty_label.setVisible(self.orgs_list.count() == 0)

            return widget

        def _create_enroll_tab(self) -> QWidget:
            """Build the Enrollment tab.

            Mirrors the Restore tab's groupbox pattern: three logical sections
            (Organization & device / WiFi / Actions), with the primary action
            (Make Supervised) visually distinct from the secondary ones.
            """
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
            use_device_btn.clicked.connect(self._use_selected_device)
            udid_row.addWidget(use_device_btn)
            org_form.addRow("Device UDID:", udid_row)

            layout.addWidget(org_box)

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

            # ----- Actions row (primary + secondary) -----
            actions_row = QHBoxLayout()
            actions_row.setSpacing(8)

            # Primary action — bold + taller so the eye lands on it.
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

            self.validate_btn = QPushButton("Validate Prerequisites")
            self.validate_btn.clicked.connect(self._validate_prereqs)
            actions_row.addWidget(self.validate_btn)

            self.check_status_btn = QPushButton("Check Status")
            self.check_status_btn.clicked.connect(self._check_status)
            actions_row.addWidget(self.check_status_btn)

            self.prepare_reenroll_btn = QPushButton("Prepare Re-Enrollment")
            self.prepare_reenroll_btn.clicked.connect(self._prepare_reenroll)
            actions_row.addWidget(self.prepare_reenroll_btn)

            actions_row.addStretch()
            layout.addLayout(actions_row)

            layout.addStretch(1)

            return widget

        def _create_restore_tab(self) -> QWidget:
            """Build the Restore tab.

            Layout (top + bottom in a vertical QSplitter):

              ┌── top: setup ──────────────────────────────────┐
              │ Device groupbox:  Device / ProductType / Mode  │
              │ Firmware groupbox: Cache / Version / IPSW / V  │
              │ Actions row: primary Start + secondary recov  │
              ├─────────────── splitter ────────────────────────┤
              └── bottom: status (anchored, always visible) ──┘
                Progress bar (fixed-height) + Activity log
            """
            outer = QWidget()
            outer_layout = QVBoxLayout(outer)
            outer_layout.setContentsMargins(8, 8, 8, 8)

            splitter = QSplitter(Qt.Orientation.Vertical)
            outer_layout.addWidget(splitter, 1)

            # ---------- TOP: setup ----------
            setup_widget = QWidget()
            setup_layout = QVBoxLayout(setup_widget)
            setup_layout.setContentsMargins(0, 0, 0, 0)
            setup_layout.setSpacing(8)

            # ----- Device groupbox -----
            device_box = QGroupBox("Device")
            device_layout = QFormLayout(device_box)
            device_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            self.restore_device_combo = QComboBox()
            self.restore_device_combo.currentIndexChanged.connect(
                self._on_restore_device_changed
            )
            device_layout.addRow("Device:", self.restore_device_combo)

            self.restore_product_type_label = QLabel("<select a device>")
            device_layout.addRow("ProductType:", self.restore_product_type_label)

            self.restore_device_mode_label = QLabel("—")
            device_layout.addRow("Mode:", self.restore_device_mode_label)

            setup_layout.addWidget(device_box)

            # ----- Firmware groupbox -----
            firmware_box = QGroupBox("Firmware")
            firmware_layout = QFormLayout(firmware_box)
            firmware_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

            cache_row = QHBoxLayout()
            self.restore_cache_path_label = QLabel(str(resolve_cache_dir()))
            self.restore_cache_path_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            cache_row.addWidget(self.restore_cache_path_label, 1)
            cache_folder_btn = QPushButton("Change...")
            cache_folder_btn.clicked.connect(self._pick_cache_folder)
            cache_row.addWidget(cache_folder_btn)
            show_cache_btn = QPushButton("Show cache")
            show_cache_btn.clicked.connect(self._show_cache)
            cache_row.addWidget(show_cache_btn)
            self.restore_clear_cache_btn = QPushButton("Clear cache")
            self.restore_clear_cache_btn.clicked.connect(self._clear_restore_cache)
            cache_row.addWidget(self.restore_clear_cache_btn)
            firmware_layout.addRow("Cache:", cache_row)

            version_row = QHBoxLayout()
            self.restore_versions_combo = QComboBox()
            version_row.addWidget(self.restore_versions_combo, 1)
            self.restore_refresh_versions_btn = QPushButton("Refresh versions")
            self.restore_refresh_versions_btn.clicked.connect(self._refresh_versions)
            self.restore_refresh_versions_btn.setEnabled(False)
            version_row.addWidget(self.restore_refresh_versions_btn)
            firmware_layout.addRow("iOS version:", version_row)

            ipsw_row = QHBoxLayout()
            self.restore_ipsw_path_label = QLabel("<not selected>")
            self.restore_ipsw_path_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            ipsw_row.addWidget(self.restore_ipsw_path_label, 1)
            browse_ipsw_btn = QPushButton("Browse...")
            browse_ipsw_btn.clicked.connect(self._browse_ipsw)
            ipsw_row.addWidget(browse_ipsw_btn)
            self.restore_verify_btn = QPushButton("Verify (ipsw.me)")
            self.restore_verify_btn.clicked.connect(self._verify_ipsw)
            self.restore_verify_btn.setEnabled(False)
            ipsw_row.addWidget(self.restore_verify_btn)
            firmware_layout.addRow("IPSW:", ipsw_row)

            setup_layout.addWidget(firmware_box)

            # ----- Actions row (primary + secondary) -----
            actions_row = QHBoxLayout()
            actions_row.setSpacing(8)

            # Primary action: bold + taller so it visually stands out from
            # the secondary recovery buttons.
            self.restore_start_btn = QPushButton("Start Restore")
            self.restore_start_btn.setObjectName("restore_start_btn")
            f = self.restore_start_btn.font()
            f.setBold(True)
            self.restore_start_btn.setFont(f)
            self.restore_start_btn.setMinimumHeight(36)
            self.restore_start_btn.clicked.connect(self._start_restore)
            self.restore_start_btn.setEnabled(False)
            actions_row.addWidget(self.restore_start_btn)

            actions_row.addSpacing(16)

            self.restore_refresh_devices_btn = QPushButton("Refresh Devices")
            self.restore_refresh_devices_btn.clicked.connect(self._refresh_devices)
            actions_row.addWidget(self.restore_refresh_devices_btn)

            self.restore_enter_recovery_btn = QPushButton("Enter Recovery")
            self.restore_enter_recovery_btn.clicked.connect(self._enter_recovery)
            self.restore_enter_recovery_btn.setEnabled(False)
            actions_row.addWidget(self.restore_enter_recovery_btn)

            self.restore_exit_recovery_btn = QPushButton("Exit Recovery")
            self.restore_exit_recovery_btn.clicked.connect(self._exit_recovery)
            self.restore_exit_recovery_btn.setEnabled(False)
            actions_row.addWidget(self.restore_exit_recovery_btn)

            # Fallback: a recovery device on the bus but the selection box
            # is empty (recovery devices are invisible to usbmuxd). The
            # primary Exit Recovery needs a selected device.
            self.restore_exit_recovery_any_btn = QPushButton("Exit Recovery (any)")
            self.restore_exit_recovery_any_btn.clicked.connect(self._exit_recovery_any)
            actions_row.addWidget(self.restore_exit_recovery_any_btn)

            actions_row.addStretch()
            setup_layout.addLayout(actions_row)

            # ---------- BOTTOM: status (anchored) ----------
            status_widget = QWidget()
            status_layout = QVBoxLayout(status_widget)
            status_layout.setContentsMargins(0, 4, 0, 0)
            status_layout.setSpacing(4)

            self.restore_progress_bar = QProgressBar()
            self.restore_progress_bar.setObjectName("restore_progress_bar")
            self.restore_progress_bar.setFixedHeight(22)
            self.restore_progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.restore_progress_bar.setTextVisible(True)
            # Always visible so the layout doesn't reflow when a restore
            # starts. Idle state: determinate 0% with a "Ready" label. On
            # restore start the bar switches to indeterminate ("Working...")
            # via _reset_restore_progress_bar, then to determinate on the
            # first real progress event.
            self.restore_progress_bar.setRange(0, 100)
            self.restore_progress_bar.setValue(0)
            self.restore_progress_bar.setFormat("Ready")
            status_layout.addWidget(self.restore_progress_bar)

            log_label = QLabel("Activity log")
            log_label.setStyleSheet("color: palette(mid); font-weight: 600;")
            status_layout.addWidget(log_label)

            self.restore_log_text = QTextEdit()
            self.restore_log_text.setReadOnly(True)
            self.restore_log_text.setObjectName("restore_log_text")
            status_layout.addWidget(self.restore_log_text, 1)

            splitter.addWidget(setup_widget)
            splitter.addWidget(status_widget)
            # ~60/40 default split; user can drag. Status never collapses
            # below 120 px so the bar + at least a few log lines stay
            # visible.
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
            splitter.setSizes([460, 280])
            splitter.setChildrenCollapsible(False)

            return outer

        def _log(self, message: str) -> None:
            self.log_signal.emit(message)

        def _append_log(self, message: str) -> None:
            self.log_text.append(message)

        def _log_to_restore(self, message: str) -> None:
            """Append ``message`` to the Restore tab's log panel.

            Emits a signal (like ``_log``) so calls from a worker thread are
            delivered on the GUI thread — direct ``QTextEdit`` access from a
            QThread is unsafe.
            """
            self.restore_log_signal.emit(message)

        def _append_restore_log(self, message: str) -> None:
            self.restore_log_text.append(message)

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

        def _update_org_details(self, current_row: int) -> None:
            """Render the selected org's fields in the read-only details pane.

            Connected to ``orgs_list.currentRowChanged`` in ``_create_orgs_tab``.
            Called with ``-1`` when the list becomes empty (e.g. after delete).
            """
            org = self._selected_org()
            if not org:
                self.orgs_details_label.setText("(no organization selected)")
                return
            has_identity = bool(org.cert_path and org.key_path)
            lines = [
                f"<b>{org.name}</b>",
                f"Org ID: {org.org_id or '(none)'}",
                f"MDM URL: {org.mdm_url or '(none)'}",
                f"Check-in URL: {org.checkin_url or '(none)'}",
                f"MDM Topic: {org.mdm_topic or '(none)'}",
                f"Identity: {'yes' if has_identity else 'no'}",
                f"WiFi config: {org.wifi_config_path or '(none)'}",
                f"Created: {org.created_at}",
            ]
            self.orgs_details_label.setText("<br>".join(lines))

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
            buttons = [self.refresh_devices_btn]
            restore_btn = getattr(self, "restore_refresh_devices_btn", None)
            if restore_btn is not None:
                buttons.append(restore_btn)
            worker = WorkerThread(list_devices)
            self._run_worker(worker, self._on_devices_refreshed, buttons, token=token)

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
            # Toggle the empty-state placeholder to match the list state.
            self.devices_empty_label.setVisible(self.devices_list.count() == 0)
            if self._devices:
                self.devices_list.setCurrentRow(0)
            self._update_enroll_udids()
            self._populate_restore_device_combo()
            self._update_mode_labels()
            self._log(f"Found {len(self._devices)} device(s).")
            self._update_status_bar()

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
                has_identity = bool(org.cert_path and org.key_path)
                badge = (
                    _format_cert_expiry_badge(_cert_expiry(org.cert_path))
                    if has_identity
                    else ""
                )
                display = (
                    f"{org.name}  (MDM: {org.mdm_url or 'none'}, "
                    f"identity: {'yes' if has_identity else 'no'}{badge})"
                )
                QListWidgetItem(display, self.orgs_list)
            self.orgs_empty_label.setVisible(self.orgs_list.count() == 0)
            if self._orgs:
                self.orgs_list.setCurrentRow(0)
            self._update_enroll_orgs()
            self._log(f"Found {len(self._orgs)} organization(s).")
            self._update_status_bar()

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

        def _edit_org(self) -> None:
            """Entry point for the 'Edit Org' button.

            Opens a dialog pre-filled with the selected org's editable fields
            (org_id, mdm_url, checkin_url, mdm_topic, cert_path). The key is
            intentionally not editable here — use 'Generate Identity' to
            regenerate both cert and key together (security-sensitive).
            """
            org = self._selected_org()
            if not org:
                QMessageBox.warning(self, "No organization", "Select an organization first.")
                return
            dialog, fields = self._build_edit_org_form(org)
            button_box = dialog.findChild(QDialogButtonBox)
            assert button_box is not None  # we just added it

            def save() -> None:
                self._apply_edit_org(org, fields, dialog)

            button_box.accepted.connect(save)
            button_box.rejected.connect(dialog.reject)
            dialog.exec()

        def _import_org(self) -> None:
            """Entry point for the 'Import…' button.

            Routes to OrganizationManager.import_org (Apple Configurator
            .organization file) or .import_mobileconfig (MDM .mobileconfig)
            based on the file extension. Both run on a worker thread so the
            GUI stays responsive on large .organization files.
            """
            path_str, _ = QFileDialog.getOpenFileName(
                self,
                "Import organization",
                "",
                "All supported (*.organization *.mobileconfig);;"
                "Apple Configurator (*.organization);;"
                "Mobileconfig (*.mobileconfig);;"
                "All Files (*)",
            )
            if not path_str:
                return
            path = Path(path_str)
            manager = OrganizationManager()

            def work() -> Organization:
                if path.suffix.lower() == ".mobileconfig":
                    return manager.import_mobileconfig(path)
                return manager.import_org(path)

            def on_done(result: Organization, error: Exception | None) -> None:
                if error:
                    QMessageBox.warning(
                        self, "Import failed", f"Failed to import: {error}"
                    )
                    self._log(f"Import failed: {error}")
                    return
                self._log(f"Imported organization: {result.name}")
                self._refresh_orgs()

            worker = WorkerThread(work)
            self._run_worker(worker, on_done, [self.import_org_btn])

        def _export_org(self) -> None:
            """Entry point for the 'Export…' button.

            Opens a Save-As dialog (defaulting to <org-name>.zip) and calls
            OrganizationManager.export_org(name, dest) on a worker thread.
            The destination can be a .zip file or a directory path — both
            are supported by export_org.
            """
            org = self._selected_org()
            if not org:
                QMessageBox.warning(
                    self, "No organization", "Select an organization first."
                )
                return
            default_name = f"{org.name}.zip"
            path_str, _ = QFileDialog.getSaveFileName(
                self,
                "Export organization",
                default_name,
                "Zip (*.zip);;Directory (use a folder name)",
            )
            if not path_str:
                return
            dest = Path(path_str)

            def work() -> bool:
                return OrganizationManager().export_org(org.name, dest)

            def on_done(result: bool, error: Exception | None) -> None:
                if error:
                    QMessageBox.warning(
                        self, "Export failed", f"Failed to export: {error}"
                    )
                    self._log(f"Export failed: {error}")
                    return
                if not result:
                    QMessageBox.warning(
                        self, "Export failed", "export_org returned False"
                    )
                    return
                self._log(f"Exported organization: {org.name} → {dest}")
                QMessageBox.information(
                    self, "Export complete", f"Exported to {dest}"
                )

            worker = WorkerThread(work)
            self._run_worker(worker, on_done, [self.export_org_btn])

        def _attach_wifi(self) -> None:
            """Entry point for the 'Attach WiFi…' button.

            Wraps ``set_org_wifi`` from cli_actions so the user can attach a
            .mobileconfig file to an org without dropping to the CLI. The
            Enrollment tab already auto-populates WiFi fields from a
            configured org, so attaching here closes that loop.

            Replaces an existing WiFi config after confirmation (matching
            the CLI's ``org set-wifi --yes`` flow). Runs on a worker thread
            because plist parsing + file copy can be slow on cold caches.
            """
            org = self._selected_org()
            if not org:
                QMessageBox.warning(
                    self, "No organization", "Select an organization first."
                )
                return
            path_str, _ = QFileDialog.getOpenFileName(
                self,
                "Choose WiFi mobileconfig",
                "",
                "Mobileconfig (*.mobileconfig);;All Files (*)",
            )
            if not path_str:
                return
            wifi_path = Path(path_str)
            if org.wifi_config_path:
                reply = QMessageBox.question(
                    self,
                    "Replace WiFi config?",
                    (
                        f"Replace existing WiFi config on '{org.name}'?\n\n"
                        f"Old: {org.wifi_config_path}\nNew: {wifi_path}"
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            manager = OrganizationManager()

            def work() -> Any:
                return set_org_wifi(manager, org.name, str(wifi_path))

            def on_done(result: Any, error: Exception | None) -> None:
                if error:
                    if isinstance(error, OrgNotFoundError):
                        msg = f"Organization not found: {org.name}"
                    elif isinstance(error, WifiConfigNotFoundError):
                        msg = f"WiFi config not found: {wifi_path}"
                    elif isinstance(error, WifiConfigInvalidError):
                        msg = f"Invalid mobileconfig: {wifi_path}"
                    else:
                        msg = f"Failed to attach WiFi: {error}"
                    QMessageBox.warning(self, "Attach failed", msg)
                    self._log(f"Attach WiFi failed: {error}")
                    return
                self._log(f"Attached WiFi to '{org.name}': {result.wifi_config_path}")
                self._refresh_orgs()

            worker = WorkerThread(work)
            self._run_worker(worker, on_done, [self.attach_wifi_btn])

        def _build_edit_org_form(self, org: Organization) -> tuple[QDialog, dict[str, QLineEdit]]:
            """Construct the Edit Org dialog with QLineEdit fields pre-filled.

            Returns (dialog, fields) where fields maps field names to their
            QLineEdit widgets. Tests use this to verify pre-fill behavior
            without spinning up the dialog's event loop.

            Cert path is editable via a 'Browse...' button (QFileDialog). The
            key is intentionally not exposed — use 'Generate Identity'.
            """
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Edit Organization — {org.name}")
            dialog.setModal(True)
            layout = QFormLayout(dialog)

            org_id_edit = QLineEdit(org.org_id or "")
            mdm_url_edit = QLineEdit(org.mdm_url or "")
            checkin_url_edit = QLineEdit(org.checkin_url or "")
            mdm_topic_edit = QLineEdit(org.mdm_topic or "")
            cert_path_edit = QLineEdit(org.cert_path or "")

            cert_browse = QPushButton("Browse…")
            cert_row = QHBoxLayout()
            cert_row.addWidget(cert_path_edit, 1)
            cert_row.addWidget(cert_browse)

            def pick_cert() -> None:
                path, _ = QFileDialog.getOpenFileName(
                    dialog,
                    "Choose certificate (DER)",
                    "",
                    "DER (*.der);;All Files (*)",
                )
                if path:
                    cert_path_edit.setText(path)

            cert_browse.clicked.connect(pick_cert)

            layout.addRow("Org ID:", org_id_edit)
            layout.addRow("MDM URL:", mdm_url_edit)
            layout.addRow("Check-in URL:", checkin_url_edit)
            layout.addRow("MDM Topic:", mdm_topic_edit)
            layout.addRow("Certificate:", cert_row)

            button_box = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
            )
            layout.addRow(button_box)

            fields = {
                "org_id": org_id_edit,
                "mdm_url": mdm_url_edit,
                "checkin_url": checkin_url_edit,
                "mdm_topic": mdm_topic_edit,
                "cert_path": cert_path_edit,
            }
            return dialog, fields

        def _apply_edit_org(
            self,
            org: Organization,
            fields: dict[str, QLineEdit],
            dialog: QDialog,
        ) -> None:
            """Validate form, build a fresh Organization, and save it.

            Split from ``_edit_org`` so tests can drive it directly without
            needing to mock QDialog.exec. On invalid input: shows a warning
            and returns without saving. On save success: closes the dialog
            and refreshes the org list.
            """
            name = org.name  # immutable from this dialog
            mdm_url = fields["mdm_url"].text().strip() or None
            checkin_url = fields["checkin_url"].text().strip() or None
            mdm_topic = fields["mdm_topic"].text().strip() or None
            try:
                validate_org_fields(name, mdm_url, checkin_url, mdm_topic)
            except OrgValidationError as exc:
                QMessageBox.warning(dialog, "Invalid input", str(exc))
                return

            new_cert_path = fields["cert_path"].text().strip() or org.cert_path
            updated = Organization(
                name=name,
                org_id=fields["org_id"].text().strip() or None,
                mdm_url=mdm_url,
                checkin_url=checkin_url,
                mdm_topic=mdm_topic,
                cert_path=new_cert_path,
                key_path=org.key_path,  # not editable here
                wifi_config_path=org.wifi_config_path,  # preserved
            )
            try:
                OrganizationManager().save_org(updated, overwrite=True)
                self._log(f"Updated organization: {name}")
                dialog.accept()
                self._refresh_orgs()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(dialog, "Save failed", f"Failed to update: {exc}")
                self._log(f"Failed to update organization: {exc}")

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

        def _guided_enroll(self) -> None:
            """Single-click enrollment: validate → confirm → run.

            The single highest-value workflow in the GUI — pick an org,
            pick a device, fill WiFi (optional), click Guided Enroll.
            Shows a confirm dialog summarizing the resolved parameters
            before kicking off ``make_supervised`` on a worker thread.

            Differs from ``_make_supervised`` only in UX: same engine call,
            but with a confirmation step that mirrors the CLI's
            ``guided-enroll`` interactive flow.
            """
            org = self._resolve_enroll_org()
            if not org:
                return
            if not org.cert_path or not org.key_path:
                QMessageBox.warning(
                    self,
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
                QMessageBox.warning(self, "No device", "Select a device UDID.")
                return
            try:
                skip_list = resolve_skip_panes(
                    self.enroll_preset_combo.currentText(), None
                )
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid preset", str(exc))
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
                self,
                "Confirm Guided Enrollment",
                summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            self._log(f"Guided enrollment starting for {udid}...")

            def progress(msg: str) -> None:
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
            self._run_worker(
                worker,
                self._on_make_supervised_result,
                [self.guided_enroll_btn, self.make_supervised_btn],
            )

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

        def _populate_restore_device_combo(self) -> None:
            """Fill the Restore tab's device dropdown from ``self._devices``.

            Stores the UDID as the item's userData so the selected item
            resolves to a device without re-parsing display text. When a
            Recovery-mode device is on the USB bus — invisible to usbmuxd, so
            missing from ``self._devices`` — a synthetic "Recovery mode"
            entry is appended with the descriptor's SRNM serial as userData.
            ``exit_recovery_mode`` / ``_device_ecid`` resolve that serial to
            the actual device (SRNM match), so the recovery buttons and the
            Start button work for it.
            """
            self.restore_device_combo.blockSignals(True)
            try:
                self.restore_device_combo.clear()
                self._restore_is_recovery = False
                for device in self._devices:
                    self.restore_device_combo.addItem(
                        f"{device.device_name}  ({device.udid})",
                        userData=device.udid,
                    )
                if detect_recovery_devices_present():
                    recovery = recovery_device_descriptor()
                    if recovery is not None:
                        srnm, ecid = recovery
                        label = f"{srnm or 'Recovery device'} (Recovery mode)"
                        self.restore_device_combo.addItem(
                            label,
                            userData=srnm or ecid,
                        )
            finally:
                self.restore_device_combo.blockSignals(False)
            self._on_restore_device_changed(self.restore_device_combo.currentIndex())

        def _on_restore_device_changed(self, index: int) -> None:
            """Update the ProductType/Mode labels and gate the action buttons."""
            self.restore_versions_combo.clear()
            self.restore_start_btn.setEnabled(False)
            self.restore_refresh_versions_btn.setEnabled(False)
            self._restore_is_recovery = False
            has_device = index >= 0
            self.restore_enter_recovery_btn.setEnabled(has_device)
            self.restore_exit_recovery_btn.setEnabled(has_device)
            if not has_device:
                self.restore_product_type_label.setText("<select a device>")
                # The previous selection's firmware pick (browsed IPSW, verify
                # state, disabled versions combo) no longer applies.
                self._restore_selected_udid = None
                self._reset_restore_firmware_selection()
                self._update_mode_labels()
                return
            udid = self.restore_device_combo.currentData()
            # A locally-browsed IPSW (and the disabled version combo it
            # causes) belongs to the previously selected device — drop it
            # when the target changes so Start/Verify can't act on a stale
            # path. Kept on same-device refreshes (re-enumeration after
            # recovery exit) so a picked file survives a refresh.
            if udid != self._restore_selected_udid:
                self._reset_restore_firmware_selection()
            self._restore_selected_udid = udid
            device = next((d for d in self._devices if d.udid == udid), None)
            if device is None:
                # Could be the synthetic "(Recovery mode)" entry (SRNM/ECID as
                # userData). Recovery devices are invisible to usbmuxd, so they
                # never land in self._devices. Treat the selection as a
                # recovery device so the user can still drive a restore
                # (targeted by ECID).
                if detect_recovery_devices_present():
                    rec = recovery_device_descriptor()
                    if rec is not None and udid in (rec[0], rec[1]):
                        self._restore_is_recovery = True
                        self.restore_product_type_label.setText("Recovery mode")
                        self.restore_refresh_versions_btn.setEnabled(False)
                        self._update_mode_labels()  # shows "Recovery"
                        # "Find firmwares": populate the versions combo with any
                        # cached IPSW files so the user can pick one without
                        # browsing.
                        self._load_cached_ipsw_for_recovery()
                        return
                self.restore_product_type_label.setText("<unknown>")
                self._update_mode_labels()
                return
            product_type = getattr(device, "device_type", "") or ""
            if product_type:
                self.restore_product_type_label.setText(product_type)
            else:
                self.restore_product_type_label.setText("<unknown>")
            # Always enable refresh: when the device list lacks a ProductType,
            # _refresh_versions falls back to reading it from lockdown.
            self.restore_refresh_versions_btn.setEnabled(True)
            self._update_mode_labels()

        def _reset_restore_firmware_selection(self) -> None:
            """Clear a locally-picked IPSW when the restore target changes.

            A browsed file (or the disabled version combo that comes with it)
            describes the previously selected device. Resetting keeps Start
            Restore and Verify from acting on a stale path, and re-enables the
            versions combo so the new device can use the signed-version flow.
            """
            self._restore_ipsw_path = None
            self.restore_ipsw_path_label.setText("<not selected>")
            self.restore_versions_combo.setEnabled(True)
            self._update_restore_verify_enabled()

        def _load_cached_ipsw_for_recovery(self) -> None:
            """Populate the versions combo with cached .ipsw files (Recovery mode).

            A device in Recovery mode can't fetch signed versions over lockdown,
            so instead surface locally-cached IPSW files from the cache dir and
            enable Start if any are present.
            """
            cache_dir = resolve_cache_dir()
            ipsws = sorted(cache_dir.glob("*.ipsw")) if cache_dir and cache_dir.is_dir() else []
            self.restore_versions_combo.clear()
            for p in ipsws:
                self.restore_versions_combo.addItem(p.name, userData=str(p))
            if ipsws:
                self.restore_versions_combo.setCurrentIndex(0)
                self._restore_ipsw_path = ipsws[0]
                self.restore_ipsw_path_label.setText(str(ipsws[0]))
                self.restore_start_btn.setEnabled(True)
                # The cached file can be hashed against ipsw.me too.
                self._update_restore_verify_enabled()
                self._log_to_restore(f"Found {len(ipsws)} cached IPSW(s) in {cache_dir}.")
            else:
                self._restore_ipsw_path = None
                self._log_to_restore(
                    f"No cached IPSW in {cache_dir}. Use 'Browse...' to pick one."
                )

        def _update_mode_labels(self) -> None:
            """Refresh the Restore tab's device-mode label.

            Detects the USB mode (normal/recovery/restore/dfu/unknown) of the
            currently selected device. Called after every device-list refresh
            and device-selection change so the label tracks mode changes
            (e.g. after entering/exiting recovery). With no device selected,
            also re-evaluates whether a recovery-mode device on the USB bus
            makes the Start button usable.
            """
            udid = self.restore_device_combo.currentData()
            if not udid:
                self.restore_device_mode_label.setText("—")
                self._update_restore_start_for_recovery()
                return
            if self._restore_is_recovery:
                self.restore_device_mode_label.setText("Recovery")
                return
            try:
                mode = detect_device_mode(udid)
            except Exception as exc:  # noqa: BLE001
                self._log_to_restore(f"Could not detect device mode: {exc}")
                mode = "unknown"
            self.restore_device_mode_label.setText(mode)

        def _update_restore_start_for_recovery(self) -> None:
            """Enable Start when a Recovery-mode device is on the USB bus.

            Such a device is invisible to usbmuxd (lockdown isn't running),
            so it never lands in ``restore_device_combo``. When one is
            present, the Start button must still work — ``_start_restore``
            resolves the device's ECID and restores via ``-i``.
            """
            if self.restore_device_combo.currentData() is not None:
                return
            try:
                if detect_recovery_devices_present():
                    self.restore_start_btn.setEnabled(True)
            except Exception:  # noqa: BLE001
                pass

        def _refresh_versions(self) -> None:
            udid = self.restore_device_combo.currentData()
            if not udid:
                QMessageBox.warning(self, "No device", "Select a device first.")
                return
            device = next((d for d in self._devices if d.udid == udid), None)
            product_type = device.device_type if device and device.device_type else ""
            if not product_type:
                # The device list didn't carry a ProductType — ask lockdown directly.
                self._log_to_restore(f"Reading ProductType from {udid}...")
                worker = WorkerThread(lambda: get_product_type_for_udid(udid))
                self._run_worker(
                    worker,
                    self._on_product_type_resolved,
                    [self.restore_refresh_versions_btn],
                )
                return
            self._load_versions(product_type)

        @Slot(object, object)
        def _on_product_type_resolved(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log_to_restore(f"Could not read ProductType: {error}")
                return
            self._load_versions(str(result))

        def _load_versions(self, product_type: str) -> None:
            self.restore_product_type_label.setText(product_type)
            self._log_to_restore(f"Fetching signed versions for {product_type}...")
            worker = WorkerThread(lambda: list_signed_versions(product_type))
            self._run_worker(
                worker,
                self._on_versions_refreshed,
                [self.restore_refresh_versions_btn],
            )

        @Slot(object, object)
        def _on_versions_refreshed(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log_to_restore(f"Failed to list signed versions: {error}")
                return
            versions = result or []
            cache_dir = resolve_cache_dir()
            self.restore_versions_combo.clear()
            for version in versions:
                label = version.display_label
                if cached_ipsw_path(version.url, cache_dir) is not None:
                    label = f"{label}  (cached)"
                self.restore_versions_combo.addItem(label, userData=version.url)
            self._log_to_restore(f"Found {len(versions)} signed version(s).")
            if versions:
                self.restore_versions_combo.setCurrentIndex(0)
                if not self._restore_ipsw_path:
                    self.restore_start_btn.setEnabled(True)
                self._update_restore_verify_enabled()

        def _browse_ipsw(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "Choose IPSW file", "", "iOS IPSW (*.ipsw);;All Files (*)"
            )
            if not filename:
                return
            self._restore_ipsw_path = Path(filename)
            self.restore_ipsw_path_label.setText(filename)
            self.restore_versions_combo.setEnabled(False)
            self.restore_start_btn.setEnabled(True)
            self._update_restore_verify_enabled()
            self._log_to_restore(f"Using local IPSW: {filename}")

        def _update_restore_verify_enabled(self) -> None:
            """Enable Verify when an IPSW can be resolved (local or cached)."""
            path = self._resolve_verify_ipsw_path()
            self.restore_verify_btn.setEnabled(path is not None)

        def _resolve_verify_ipsw_path(self) -> Path | None:
            """Resolve the IPSW path Verify should hash, or None.

            Order: explicit ``_restore_ipsw_path`` (Browse / recovery-cache
            pick) → versions-combo userData that is an existing local file →
            versions-combo userData that is a URL with a cache hit.
            """
            if self._restore_ipsw_path is not None and self._restore_ipsw_path.is_file():
                return self._restore_ipsw_path
            combo_data = self.restore_versions_combo.currentData()
            if not combo_data:
                return None
            candidate = Path(combo_data)
            if candidate.suffix == ".ipsw" and candidate.is_file():
                return candidate
            return cached_ipsw_path(combo_data, resolve_cache_dir())

        def _verify_ipsw(self) -> None:
            """On-demand hash verification against ipsw.me (worker thread)."""
            path = self._resolve_verify_ipsw_path()
            if path is None:
                combo_data = self.restore_versions_combo.currentData()
                if combo_data and not cached_ipsw_path(combo_data, resolve_cache_dir()):
                    QMessageBox.warning(
                        self,
                        "IPSW not cached",
                        "This version isn't downloaded yet — Start Restore to "
                        "download it, then Verify again.",
                    )
                else:
                    QMessageBox.warning(
                        self, "Nothing to verify", "Select an IPSW first."
                    )
                return

            parsed = parse_ipsw_filename(path.name)
            device = None
            build = None
            version = None
            if parsed is not None:
                device, version, build = parsed
            self._log_to_restore(
                f"Verifying {path.name} (sha1+sha256 of {path.stat().st_size:,} bytes) "
                f"against ipsw.me — this can take a minute on large IPSWs..."
            )
            self.restore_verify_btn.setEnabled(False)

            def work() -> Any:
                return verify_ipsw(path, device=device, build=build, version=version)

            worker = WorkerThread(work)
            self._run_worker(
                worker,
                self._on_verify_finished,
                [self.restore_verify_btn],
            )

        @Slot(object, object)
        def _on_verify_finished(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log_to_restore(f"Verification failed: {error}")
                QMessageBox.warning(self, "Verify failed", str(error))
                return
            v: VerifyResult = result
            self._log_to_restore(v.summary)
            if v.expected is not None:
                self._log_to_restore(
                    f"  local  sha1   {v.local_sha1}"
                )
                self._log_to_restore(
                    f"  local  sha256 {v.local_sha256}"
                )
                self._log_to_restore(
                    f"  local  size   {v.local_size:,}"
                )
                self._log_to_restore(
                    f"  ipsw.me sha1   {v.expected.sha1sum or '(n/a)'}"
                )
                self._log_to_restore(
                    f"  ipsw.me sha256 {v.expected.sha256sum or '(n/a)'}"
                )
                self._log_to_restore(
                    f"  ipsw.me size   {v.expected.filesize or '(n/a)'}"
                )
            ok = v.sha1_match and v.sha256_match and v.size_match
            if ok:
                QMessageBox.information(
                    self, "IPSW verified",
                    f"{v.path.name}\n\nSHA-1, SHA-256, and size all match "
                    "the hashes published by ipsw.me.",
                )
            elif v.expected is None:
                QMessageBox.information(
                    self, "Could not verify",
                    f"{v.path.name}\n\nCould not look up expected hashes on "
                    "ipsw.me (device/build unknown or network failure). "
                    "Local hashes were logged.",
                )
            else:
                QMessageBox.warning(
                    self, "IPSW mismatch",
                    f"{v.path.name}\n\n{v.summary}",
                )
            self._update_restore_verify_enabled()

        def _pick_cache_folder(self) -> None:
            folder = QFileDialog.getExistingDirectory(
                self, "Choose firmware cache folder", str(resolve_cache_dir())
            )
            if not folder:
                return
            try:
                write_cache_config(Path(folder))
            except Exception as exc:  # noqa: BLE001
                self._log_to_restore(f"Failed to save cache folder: {exc}")
                return
            self.restore_cache_path_label.setText(folder)
            self._log_to_restore(f"Cache folder set to {folder}")

        def _show_cache(self) -> None:
            state = cache_state(resolve_cache_dir())
            self._log_to_restore(f"Cache: {state['path']}")
            self._log_to_restore(f"  size: {state['size_bytes']:,} bytes")
            self._log_to_restore(f"  IPSW count: {state['ipsw_count']}")
            for name in state["ipsw_files"]:
                self._log_to_restore(f"    - {name}")

        def _clear_restore_cache(self) -> None:
            """Wipe the firmware cache after a confirmation prompt.

            Removes the entire cache directory (all IPSW files + manifest)
            and recreates it empty. Matches the CLI's
            ``device restore --clear-cache`` flow.
            """
            cache_dir = resolve_cache_dir()
            state = cache_state(cache_dir)
            if state["ipsw_count"] == 0:
                QMessageBox.information(
                    self, "Cache empty", "No IPSW files to clear."
                )
                return
            reply = QMessageBox.question(
                self,
                "Clear firmware cache?",
                (
                    f"Delete all {state['ipsw_count']} IPSW file(s) in {cache_dir}?\n\n"
                    f"Total size: {state['size_bytes']:,} bytes.\n"
                    "This cannot be undone."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                shutil.rmtree(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                self._log_to_restore(
                    f"Cleared firmware cache: {state['ipsw_count']} file(s), "
                    f"{state['size_bytes']:,} bytes freed."
                )
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Clear failed", str(exc))
                self._log_to_restore(f"Clear cache failed: {exc}")

        def _confirm_restore(
            self,
            target: str,
            ipsw_path: Path | None,
            version_url: str | None,
        ) -> bool:
            """Ask the user to confirm a destructive restore.

            Returns True when the user confirms. The message names the exact
            device and IPSW so there is no ambiguity about what gets erased.
            """
            if ipsw_path is not None:
                source = ipsw_path.name
            elif version_url:
                parsed = parse_ipsw_url(version_url, device="")
                source = parsed.display_label if parsed else version_url
            else:
                source = "<unknown>"
            reply = QMessageBox.question(
                self,
                "Confirm Restore",
                (
                    f"Restore will erase {target} and install:\n\n  {source}\n\n"
                    "All data on the device will be lost. This cannot be undone.\n"
                    "Continue?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        def _start_restore(self) -> None:
            udid = self.restore_device_combo.currentData()
            ecid: str | None = None
            if not udid or self._restore_is_recovery:
                # A device in Recovery mode drops off the lockdown device
                # list (invisible to usbmuxd), so it never appears in the
                # combo. Fall back to targeting it by ECID when one is on
                # the USB bus. This also fires for the synthetic "(Recovery
                # mode)" combo entry, whose userData is an SRNM/ECID rather
                # than a real UDID.
                if detect_recovery_devices_present():
                    ecid = _device_ecid()
                    if not ecid:
                        QMessageBox.warning(
                            self,
                            "Recovery device",
                            "A device is in Recovery mode but its ECID could "
                            "not be determined. Run `irecovery -q` on the "
                            "device and retry.",
                        )
                        return
                    udid = None
                    self._log_to_restore(
                        f"Device in Recovery mode detected — restoring via ECID {ecid}."
                    )
                else:
                    QMessageBox.warning(self, "No device", "Select a device UDID or connect a device.")
                    return

            local_path = self._restore_ipsw_path
            version_url = self.restore_versions_combo.currentData()
            if local_path is None and version_url:
                # The combo may carry a cached .ipsw path (Recovery mode
                # selection) rather than a download URL — treat an existing
                # local file as such.
                cand = Path(version_url)
                if cand.suffix == ".ipsw" and cand.is_file():
                    local_path = cand
            if local_path is not None:
                if not local_path.is_file():
                    QMessageBox.warning(
                        self, "IPSW missing", f"IPSW file not found: {local_path}"
                    )
                    return
                ipsw_path: Path | None = local_path
            elif version_url:
                parsed = parse_ipsw_url(version_url, device="")
                if parsed is not None:
                    self._log_to_restore(f"Restoring {parsed.display_label} ...")
                ipsw_path = None
            else:
                QMessageBox.warning(
                    self,
                    "No IPSW",
                    "Pick a signed version (Refresh versions) or a local .ipsw file.",
                )
                return

            cache_dir = resolve_cache_dir()
            if ipsw_path is None:
                self._log_to_restore(f"Downloading {version_url} to {cache_dir} ...")

            def on_progress(event: ProgressEvent) -> None:
                # Runs on the worker thread — hand the event to the GUI thread
                # via a queued signal (logging + QProgressBar are not thread-safe).
                self.restore_progress_signal.emit(event)
                if (
                    event.progress is not None
                    and event.progress.label == "Using cached IPSW"
                ):
                    self._log_to_restore(
                        "Using cached IPSW at "
                        f"{cache_dir / _filename_from_url(version_url)}"
                    )

            def work() -> Any:
                target = (
                    ipsw_path
                    if ipsw_path is not None
                    else download_ipsw(
                        version_url,
                        cache_dir,
                        progress_callback=on_progress,
                    )
                )
                return engine_restore_device(
                    udid=udid,
                    ipsw_path=target,
                    cache_dir=cache_dir,
                    progress_callback=on_progress,
                    ecid=ecid,
                )

            target_label = udid or (f"ECID {ecid}" if ecid else "<unknown>")
            # A restore wipes the device — confirm first, matching the CLI's
            # ``typer.confirm("Erase and restore device now?")`` and the other
            # destructive GUI actions (delete org, prepare re-enrollment,
            # enter recovery) which all ask before acting.
            if not self._confirm_restore(target_label, ipsw_path, version_url):
                self._log_to_restore("Restore cancelled.")
                return
            self._reset_restore_progress_bar()
            self._log_to_restore(
                f"Restore starting for {target_label} (cache: {cache_dir})."
            )
            worker = WorkerThread(work)
            # Gate every action that would race the restore's USB access:
            # a second restore, recovery-mode transitions, refreshing
            # versions, and hashing the IPSW all talk to the same device.
            self._run_worker(
                worker,
                self._on_restore_finished,
                [
                    self.restore_start_btn,
                    self.restore_enter_recovery_btn,
                    self.restore_exit_recovery_btn,
                    self.restore_exit_recovery_any_btn,
                    self.restore_refresh_versions_btn,
                    self.restore_verify_btn,
                ],
            )

        def _reset_restore_progress_bar(self) -> None:
            """Put the Restore bar into its indeterminate 'working' state."""
            self._restore_step_label = None
            self._restore_last_percent = 0
            self.restore_progress_bar.setRange(0, 0)
            self.restore_progress_bar.setFormat("Working...")
            self.restore_progress_bar.setVisible(True)

        @staticmethod
        def _normalize_step_label(label: str) -> str:
            """Strip a leading 'Restoring ' so the format reads naturally.

            ``idevicerestore -P`` emits ``STEP: Restoring Baseband``; the bar
            format is ``Restoring {label} 45%``, so the label should be
            ``Baseband``, not ``Restoring Baseband``.
            """
            for prefix in ("Restoring ", "restoring "):
                if label.startswith(prefix):
                    return label[len(prefix):]
            return label

        def _update_restore_progress_format(self) -> None:
            """Refresh the bar's format string from the current value + step."""
            bar = self.restore_progress_bar
            value = bar.value()
            label = self._restore_step_label
            if value >= 100:
                bar.setFormat(f"Step complete: {label}" if label else "Step complete")
            else:
                suffix = f" {label}" if label else ""
                # %p renders the integer percentage; the trailing % is literal.
                bar.setFormat(f"Restoring{suffix} %p%")

        @Slot(object)
        def _on_restore_progress_event(self, event: ProgressEvent) -> None:
            """GUI-thread handler for one engine ``ProgressEvent``.

            Appends the raw line to the Restore log and drives the progress
            bar from the parsed update (if any). Delivered via
            ``restore_progress_signal`` so it never runs on a worker thread.
            """
            self._append_restore_log(f"  {event.text}")
            update = event.progress
            if update is None:
                return
            bar = self.restore_progress_bar
            if bar.maximum() == 0:
                # First real progress event: switch from indeterminate to
                # determinate. setValue(0) also kicks Qt out of its busy
                # mode, which is what makes the format text render again.
                bar.setRange(0, 100)
                bar.setValue(0)
            if update.kind == "step":
                if update.label:
                    self._restore_step_label = self._normalize_step_label(update.label)
                    self._update_restore_progress_format()
                # idevicerestore -P often emits only STEP: lines until the
                # late per-image PROGRESS: phase. Two rules keep the bar from
                # looking frozen:
                #   1. A step that follows a phase at 100% (a completed
                #      download, or the previous step's PROGRESS hitting 30/30)
                #      opens a new progress phase — reset off the pinned 100%.
                #   2. A bare step at 0% is bumped to a 1% floor. 1% is
                #      "alive", not a real measurement, so it never overrides
                #      a genuine value.
                if self._restore_last_percent >= 100 and bar.maximum() == 100:
                    self._restore_last_percent = 0
                    bar.setValue(1)
                    self._update_restore_progress_format()
                elif (
                    bar.maximum() == 100
                    and bar.value() == 0
                    and self._restore_last_percent == 0
                ):
                    bar.setValue(1)
            elif update.kind == "percent" and update.value is not None:
                if update.label:
                    self._restore_step_label = self._normalize_step_label(update.label)
                self._restore_last_percent = update.value
                bar.setValue(update.value)
                self._update_restore_progress_format()

        def _finalize_restore_progress_bar(self, success: bool) -> None:
            """Set the bar's terminal state when the restore finishes."""
            bar = self.restore_progress_bar
            if bar.maximum() == 0:
                bar.setRange(0, 100)
                bar.setValue(0)
            if success:
                bar.setValue(100)
                bar.setFormat("Restore complete")
            else:
                bar.setFormat("Restore failed — see log")

        @Slot(object, object)
        def _on_restore_finished(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log_to_restore(f"Restore failed: {error}")
                self._finalize_restore_progress_bar(success=False)
                return
            if result is None:
                self._log_to_restore("Restore finished with no result.")
                self._finalize_restore_progress_bar(success=False)
                return
            if result.success:
                self._log_to_restore("Restore completed successfully.")
                self._finalize_restore_progress_bar(success=True)
            else:
                self._log_to_restore(f"Restore failed: {result.error}")
                self._finalize_restore_progress_bar(success=False)

        def _enter_recovery(self) -> None:
            udid = self.restore_device_combo.currentData()
            if not udid:
                return
            reply = QMessageBox.question(
                self,
                "Enter Recovery",
                "Send this device into Recovery mode? This will interrupt the "
                "running OS.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._log_to_restore(f"Entering recovery for {udid}...")
            worker = WorkerThread(lambda: enter_recovery_mode(udid))
            self._run_worker(
                worker,
                self._on_recovery_mode_result,
                [self.restore_enter_recovery_btn, self.restore_exit_recovery_btn],
            )

        def _exit_recovery(self) -> None:
            udid = self.restore_device_combo.currentData()
            if not udid:
                # A device in Recovery mode drops off the lockdown device
                # list (invisible to usbmuxd), so it never appears in the
                # combo. Fall back to the any-device reset when one is on
                # the USB bus.
                if detect_recovery_devices_present():
                    self._log_to_restore(
                        "No device selected — resetting recovery device(s) "
                        "on the USB bus..."
                    )
                    self._exit_recovery_any()
                else:
                    self._log_to_restore("No recovery device found.")
                return
            self._log_to_restore(f"Exiting recovery for {udid}...")
            worker = WorkerThread(lambda: exit_recovery_mode(udid))
            self._run_worker(
                worker,
                self._on_recovery_mode_result,
                [self.restore_enter_recovery_btn, self.restore_exit_recovery_btn],
            )

        def _exit_recovery_any(self) -> None:
            """Reset any recovery-mode device(s) on the USB bus.

            Works without a device selection because a device in Recovery mode
            is invisible to usbmuxd (lockdown isn't running) and therefore
            missing from the Restore tab's device dropdown.
            """
            self._log_to_restore("Scanning USB bus for recovery-mode devices...")
            worker = WorkerThread(lambda: exit_recovery_mode(udid=None))
            self._run_worker(
                worker,
                self._on_exit_recovery_any_result,
                [self.restore_exit_recovery_any_btn],
            )

        @Slot(object, object)
        def _on_exit_recovery_any_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log_to_restore(f"Exit Recovery failed: {error}")
            else:
                reset = result or []
                self._log_to_restore(
                    f"Reset {len(reset)} device(s) out of recovery mode."
                )
            # Refresh so the device combo + mode label reflect the new state
            # (the device reboots and re-enumerates with a different USB PID).
            self._refresh_devices()

        @Slot(object, object)
        def _on_recovery_mode_result(self, result: Any, error: Exception | None) -> None:
            if error:
                self._log_to_restore(f"Recovery mode operation failed: {error}")
                return
            self._log_to_restore("Recovery mode operation completed.")
            # Refresh so the device combo + mode label reflect the new state
            # (the device reboots and re-enumerates with a different USB PID).
            self._refresh_devices()

        def closeEvent(self, event: QEvent) -> None:
            """Refuse close while workers are still running.

            Blocking USB calls running inside ``WorkerThread.run`` cannot be
            cancelled by ``quit()`` — closing the window mid-enrollment would race
            the Python interpreter against in-flight C-level USB IO and can leave
            the device in an undefined state. Users must wait for in-flight work
            to finish (or let it fail naturally) before the window can close.

            When allowed to close, persist the window geometry so the next
            launch restores the user's preferred size/position.
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
            self._save_geometry()
            event.accept()

        def _update_status_bar(self) -> None:
            """Refresh the status bar with device/org counts and active workers.

            Cheap to call; reads only from in-memory state. Called after every
            refresh, every worker start, and every worker completion so the
            bar always reflects current reality.
            """
            devices = len(self._devices)
            orgs = len(self._orgs)
            workers = len(self._workers)
            msg = f"{devices} device(s)  •  {orgs} organization(s)"
            if workers:
                msg += f"  •  {workers} operation(s) running"
            self.statusBar().showMessage(msg)

        def _restore_geometry(self) -> None:
            """Restore window geometry from QSettings (silent on first launch)."""
            settings = QSettings("ios-enroll", "gui")
            geometry = settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
            state = settings.value("windowState")
            if state:
                self.restoreState(state)

        def _save_geometry(self) -> None:
            """Persist window geometry to QSettings (called from closeEvent)."""
            settings = QSettings("ios-enroll", "gui")
            settings.setValue("geometry", self.saveGeometry())
            settings.setValue("windowState", self.saveState())


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
_LAZY_QT_NAMES = frozenset({"WorkerThread", "EnrollmentApp", "run_gui"})


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
