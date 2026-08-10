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
from apple_device_cli.gui_qt.worker import WorkerPool
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
        QMenu,
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
            QMenu,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QSplitter,
            QStatusBar,
            QStyle,
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

    # Patch-propagation: wrap WorkerThread so instantiation looks up
    # gui_qt.WorkerThread. Tests patch gui_qt.WorkerThread = SyncWorker
    # and expect the patched version to be used. Without this wrap, code
    # in this module uses the ORIGINAL WorkerThread (this module
    # binding), bypassing the patch.
    _real_worker_thread = WorkerThread

    def _WorkerThread_delegate(fn):
        from apple_device_cli import gui_qt
        cls = getattr(gui_qt, "WorkerThread", None)
        if cls is None or cls is _WorkerThread_delegate:
            return _real_worker_thread(fn)
        return cls(fn)

    WorkerThread = _WorkerThread_delegate


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
            self._worker_pool = WorkerPool()
            # Back-compat: many tests (and a few legacy callers) read/write
            # ``self._workers`` directly. Alias to the pool's internal list so
            # append() / len() / ``in`` checks keep working without exposing
            # the pool implementation. Remove once the GUI is fully tab-split
            # and tests stop reaching into private state.
            self._workers = self._worker_pool._workers
            self._request_token: int = 0
            self._restore_ipsw_path: Path | None = None
            self._restore_selected_udid: str | None = None
            self._restore_step_label: str | None = None
            self._restore_last_percent: int = 0
            self._restore_is_recovery: bool = False

            self._setup_ui()
            self.setStatusBar(QStatusBar())
            # Keyboard shortcuts — work regardless of which tab is visible.
            # _start_restore already confirms before wiping, so Ctrl+S is safe.
            from PySide6.QtGui import QKeySequence, QShortcut
            shortcut_refresh = QShortcut(QKeySequence("Ctrl+R"), self)
            shortcut_refresh.activated.connect(self._refresh_devices)
            shortcut_guided = QShortcut(QKeySequence("Ctrl+E"), self)
            shortcut_guided.activated.connect(self._guided_enroll)
            shortcut_restore = QShortcut(QKeySequence("Ctrl+S"), self)
            shortcut_restore.activated.connect(self._start_restore)
            self.log_signal.connect(self._append_log)
            self.restore_log_signal.connect(self._append_restore_log)
            self.restore_progress_signal.connect(self._on_restore_progress_event)
            self.enroll_org_combo.currentIndexChanged.connect(self._on_enroll_org_changed)
            self.enroll_org_combo.currentIndexChanged.connect(self._update_enroll_action_gates)
            self.enroll_udid_combo.currentIndexChanged.connect(self._update_enroll_action_gates)
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

            style = self.style()
            self.tabs.addTab(
                self.devices_tab,
                style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon),
                "Devices",
            )
            self.tabs.addTab(
                self.orgs_tab,
                style.standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon),
                "Organizations",
            )
            self.tabs.addTab(
                self.enroll_tab,
                style.standardIcon(QStyle.StandardPixmap.SP_ArrowRight),
                "Enrollment",
            )
            self.tabs.addTab(
                self.restore_tab,
                style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
                "Restore",
            )

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
            # Inner padding so descenders aren't clipped at the bottom edge.
            self.log_text.setStyleSheet(
                "QTextEdit { padding: 4px 6px; }"
            )
            # Cap the log at a fixed height so a verbose operation can't
            # squeeze the tab content to zero height. Min keeps at least
            # 4 lines visible.
            self.log_text.setMaximumHeight(180)
            self.log_text.setMinimumHeight(80)
            log_layout.addWidget(self.log_text)
            layout.addWidget(log_group)

        def _create_devices_tab(self) -> QWidget:
            from apple_device_cli.gui_qt.devices_tab import DevicesTab

            self.devices_tab_controller = DevicesTab(self)
            # Mirror the controller's widgets on self so the existing
            # test suite that reads app.devices_list etc. keeps working.
            self.devices_list = self.devices_tab_controller.devices_list
            self.devices_empty_label = self.devices_tab_controller.devices_empty_label
            self.refresh_devices_btn = self.devices_tab_controller.refresh_devices_btn
            self.device_info_btn = self.devices_tab_controller.device_info_btn
            self.activate_btn = self.devices_tab_controller.activate_btn
            self.pair_btn = self.devices_tab_controller.pair_btn
            return self.devices_tab_controller.tab_widget()

        def _create_orgs_tab(self) -> QWidget:
            from apple_device_cli.gui_qt.orgs_tab import OrgsTab

            self.orgs_tab_controller = OrgsTab(self)
            self.orgs_list = self.orgs_tab_controller.orgs_list
            self.orgs_details_label = self.orgs_tab_controller.orgs_details_label
            self.orgs_empty_label = self.orgs_tab_controller.orgs_empty_label
            self.refresh_orgs_btn = self.orgs_tab_controller.refresh_orgs_btn
            self.create_org_btn = self.orgs_tab_controller.create_org_btn
            self.generate_id_btn = self.orgs_tab_controller.generate_id_btn
            self.edit_org_btn = self.orgs_tab_controller.edit_org_btn
            self.import_org_btn = self.orgs_tab_controller.import_org_btn
            self.export_org_btn = self.orgs_tab_controller.export_org_btn
            self.attach_wifi_btn = self.orgs_tab_controller.attach_wifi_btn
            self.delete_org_btn = self.orgs_tab_controller.delete_org_btn
            return self.orgs_tab_controller.tab_widget()

        def _refresh_orgs(self) -> None:
            self.orgs_tab_controller._refresh()

        @Slot(object, object)
        def _on_orgs_refreshed(self, result: Any, error: Exception | None, token: int) -> None:
            self.orgs_tab_controller._on_refreshed(result, error, token)

        def _selected_org(self) -> Organization | None:
            return self.orgs_tab_controller._selected_org()

        def _create_org_dialog(self) -> None:
            self.orgs_tab_controller._create_org_dialog()

        def _generate_identity_dialog(self) -> None:
            self.orgs_tab_controller._generate_identity_dialog()

        def _on_identity_generated(self, dialog, org, result, error) -> None:
            self.orgs_tab_controller._on_identity_generated(dialog, org, result, error)

        def _delete_org(self) -> None:
            self.orgs_tab_controller._delete_org()

        def _edit_org(self) -> None:
            self.orgs_tab_controller._edit_org()

        def _import_org(self) -> None:
            self.orgs_tab_controller._import_org()

        def _export_org(self) -> None:
            self.orgs_tab_controller._export_org()

        def _attach_wifi(self) -> None:
            self.orgs_tab_controller._attach_wifi()

        def _build_edit_org_form(self, org: Organization) -> tuple[QDialog, dict[str, QLineEdit]]:
            return self.orgs_tab_controller._build_edit_org_form(org)

        def _apply_edit_org(self, org, fields, dialog) -> None:
            self.orgs_tab_controller._apply_edit_org(org, fields, dialog)

        # Cross-tab methods: still wired up on EnrollmentApp because
        # signals + shortcuts reach in via ``app._on_enroll_org_changed``,
        # and the enroll tab's combo is built in ``_create_enroll_tab``
        # which calls ``self._update_enroll_orgs()`` after population.
        # Will move to EnrollTab in step 6.

        def _update_enroll_orgs(self) -> None:
            self.enroll_org_combo.clear()
            self.enroll_org_combo.addItems([o.name for o in self._orgs])
            if self._orgs:
                self.enroll_org_combo.setCurrentIndex(0)
            self._update_enroll_action_gates()

        @Slot(int)
        def _on_enroll_org_changed(self, index: int) -> None:
            self._enroll_org_changed(index)

        def _enroll_org_changed(self, index: int) -> None:
            """Auto-populate enrollment WiFi widgets from the selected org."""
            if index < 0:
                return
            name = self.enroll_org_combo.currentText().strip()
            if not name:
                return

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
            self._update_enroll_cert_banner(org)

        def _create_enroll_tab(self) -> QWidget:
            """Build the Enrollment tab.

            EnrollTab owns all the enroll widgets; this method just
            instantiates the controller and mirrors its widgets onto
            self for back-compat with tests.
            """
            from apple_device_cli.gui_qt.enroll_tab import EnrollTab

            self.enroll_tab_controller = EnrollTab(self)
            self.enroll_org_combo = self.enroll_tab_controller.enroll_org_combo
            self.enroll_preset_combo = self.enroll_tab_controller.enroll_preset_combo
            self.enroll_udid_combo = self.enroll_tab_controller.enroll_udid_combo
            self.enroll_cert_warning_label = self.enroll_tab_controller.enroll_cert_warning_label
            self.enroll_wifi_ssid = self.enroll_tab_controller.enroll_wifi_ssid
            self.enroll_wifi_password = self.enroll_tab_controller.enroll_wifi_password
            self.enroll_wifi_enc = self.enroll_tab_controller.enroll_wifi_enc
            self.guided_enroll_btn = self.enroll_tab_controller.guided_enroll_btn
            self.make_supervised_btn = self.enroll_tab_controller.make_supervised_btn
            self.validate_btn = self.enroll_tab_controller.validate_btn
            self.check_status_btn = self.enroll_tab_controller.check_status_btn
            self.prepare_reenroll_btn = self.enroll_tab_controller.prepare_reenroll_btn
            return self.enroll_tab_controller.tab_widget()

        def _create_restore_tab(self) -> QWidget:
            """Build the Restore tab.

            RestoreTab owns the widgets; this method instantiates the
            controller and mirrors its widgets onto self for back-compat.
            """
            from apple_device_cli.gui_qt.restore_tab import RestoreTab

            self.restore_tab_controller = RestoreTab(self)
            self.restore_device_combo = self.restore_tab_controller.restore_device_combo
            self.restore_product_type_label = self.restore_tab_controller.restore_product_type_label
            self.restore_device_mode_label = self.restore_tab_controller.restore_device_mode_label
            self.restore_cache_path_label = self.restore_tab_controller.restore_cache_path_label
            self.restore_clear_cache_btn = self.restore_tab_controller.restore_clear_cache_btn
            self.restore_versions_combo = self.restore_tab_controller.restore_versions_combo
            self.restore_refresh_versions_btn = self.restore_tab_controller.restore_refresh_versions_btn
            self.restore_ipsw_path_label = self.restore_tab_controller.restore_ipsw_path_label
            self.restore_verify_btn = self.restore_tab_controller.restore_verify_btn
            self.restore_start_btn = self.restore_tab_controller.restore_start_btn
            self.restore_refresh_devices_btn = self.restore_tab_controller.restore_refresh_devices_btn
            self.restore_enter_recovery_btn = self.restore_tab_controller.restore_enter_recovery_btn
            self.restore_exit_recovery_btn = self.restore_tab_controller.restore_exit_recovery_btn
            self.restore_exit_recovery_any_btn = self.restore_tab_controller.restore_exit_recovery_any_btn
            self.restore_progress_bar = self.restore_tab_controller.restore_progress_bar
            self.restore_empty_state_label = self.restore_tab_controller.restore_empty_state_label
            self.restore_log_text = self.restore_tab_controller.restore_log_text
            return self.restore_tab_controller.tab_widget()

        def _log(self, message: str) -> None:
            self.log_signal.emit(message)

        def _append_log(self, message: str) -> None:
            self.log_text.append(message)

        def _log_to_restore(self, message: str) -> None:
            self.restore_tab_controller._log_to_restore(message)

        def _append_restore_log(self, message: str) -> None:
            self.restore_tab_controller._append_log(message)

        def _load_initial_state(self) -> None:
            self._refresh_devices()
            self._refresh_orgs()

        def _selected_device(self) -> DeviceInfo | None:
            current = self.devices_list.currentRow()
            if current < 0 or current >= len(self._devices):
                return None
            return self._devices[current]

        def _warn_no_device(self) -> None:
            """Show the standard 'no device selected' warning + log line."""
            QMessageBox.warning(self, "No device", "Select a device first.")
            self._log("No device selected.")

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
            """Start a worker via the shared ``WorkerPool`` and gate UI buttons.

            Thin wrapper around ``self._worker_pool.submit`` so call sites
            stay readable. Token semantics match the pool: when ``token`` is
            given, ``on_finished`` receives ``(result, error, token)`` so it
            can detect stale completions against ``self._request_token``.
            """
            self._worker_pool.submit(worker, on_finished, buttons_to_disable, token=token)

        def _refresh_devices(self) -> None:
            self.devices_tab_controller._refresh()

        @Slot(object, object)
        def _on_devices_refreshed(self, result: Any, error: Exception | None, token: int) -> None:
            self.devices_tab_controller._on_refreshed(result, error, token)

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
            self._update_enroll_action_gates()

        def _use_selected_device(self) -> None:
            device = self._selected_device()
            if device:
                self.enroll_udid_combo.setCurrentText(device.udid)
            else:
                QMessageBox.warning(self, "No device", "Select a device in the Devices tab first.")
                self._log("No device selected in the Devices tab.")

        def _show_device_info(self) -> None:
            self.devices_tab_controller._show_device_info()

        @Slot(object, object)
        def _on_device_info(self, result: Any, error: Exception | None) -> None:
            self.devices_tab_controller._on_device_info(result, error)

        def _activate_device(self) -> None:
            self.devices_tab_controller._activate_device()

        @Slot(object, object)
        def _on_activation_result(self, result: Any, error: Exception | None) -> None:
            self.devices_tab_controller._on_activation_result(result, error)

        def _pair_device(self) -> None:
            self.devices_tab_controller._pair_device()

        @Slot(object, object)
        def _on_pair_result(self, result: Any, error: Exception | None) -> None:
            self.devices_tab_controller._on_pair_result(result, error)

        def _show_devices_context_menu(self, pos) -> None:
            self.devices_tab_controller._show_context_menu(pos)

        def _build_devices_context_menu(self) -> Any:
            return self.devices_tab_controller._build_context_menu()

        def _make_supervised_from_context(self) -> None:
            self.devices_tab_controller._make_supervised_from_context()

        def _resolve_enroll_org(self) -> Organization | None:
            return self.enroll_tab_controller._resolve_enroll_org()

        def _update_enroll_action_gates(self) -> None:
            self.enroll_tab_controller._update_enroll_action_gates()

        def _update_enroll_cert_banner(self, org: Organization | None) -> None:
            self.enroll_tab_controller._update_enroll_cert_banner(org)

        def _validate_prereqs(self) -> None:
            self.enroll_tab_controller._validate_prereqs()

        @Slot(object, object)
        def _on_validation_result(self, result: Any, error: Exception | None) -> None:
            self.enroll_tab_controller._on_validation_result(result, error)

        def _make_supervised(self) -> None:
            self.enroll_tab_controller._make_supervised()

        @Slot(object, object)
        def _on_make_supervised_result(self, result: Any, error: Exception | None) -> None:
            self.enroll_tab_controller._on_make_supervised_result(result, error)

        def _guided_enroll(self) -> None:
            self.enroll_tab_controller._guided_enroll()

        def _check_status(self) -> None:
            self.enroll_tab_controller._check_status()

        @Slot(object, object)
        def _on_status_result(self, result: Any, error: Exception | None) -> None:
            self.enroll_tab_controller._on_status_result(result, error)

        def _prepare_reenroll(self) -> None:
            self.enroll_tab_controller._prepare_reenroll()

        @Slot(object, object)
        def _on_reenroll_result(self, result: Any, error: Exception | None) -> None:
            self.enroll_tab_controller._on_reenroll_result(result, error)

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
                self._update_restore_empty_state()
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
            self._update_restore_empty_state()

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
                self._update_restore_empty_state()
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
                self._update_restore_empty_state()

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
            self._update_restore_empty_state()
            self._log_to_restore(f"Using local IPSW: {filename}")

        def _update_restore_verify_enabled(self) -> None:
            """Enable Verify when an IPSW can be resolved (local or cached)."""
            path = self._resolve_verify_ipsw_path()
            self.restore_verify_btn.setEnabled(path is not None)

        def _update_restore_empty_state(self) -> None:
            """Hide the empty-state hint once the user has anything to act on.

            The hint disappears when any of these is populated:
              • a device is selected in restore_device_combo
              • an IPSW is browsed (_restore_ipsw_path is a real file)
              • a signed version is in restore_versions_combo
            """
            has_device = bool(self.restore_device_combo.currentData())
            has_ipsw = (
                self._restore_ipsw_path is not None
                and self._restore_ipsw_path.is_file()
            )
            has_version = self.restore_versions_combo.count() > 0
            self.restore_empty_state_label.setHidden(
                has_device or has_ipsw or has_version
            )

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
            self.restore_tab_controller._clear_restore_cache()

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
                self._record_last_op(
                    f"Restored to {(self.restore_versions_combo.currentText() or 'local IPSW').strip()}"
                )
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
            if self._worker_pool:
                busy = len(self._worker_pool)
                QMessageBox.warning(
                    self,
                    "Operations in progress",
                    f"{busy} operation(s) are still running. Wait for them to "
                    f"finish before closing the window.",
                )
                event.ignore()
                return
            self._save_geometry()
            event.accept()

        def _update_status_bar(self) -> None:
            """Refresh the status bar with device/org counts, workers, and last op.

            Cheap to call; reads only from in-memory state + QSettings.
            Called after every refresh, every worker start, and every
            worker completion so the bar always reflects current reality.
            """
            self._refresh_status_bar_with_last()

        def _status_bar_base_text(self) -> str:
            """The non-'last operation' part of the status bar message."""
            devices = len(self._devices)
            orgs = len(self._orgs)
            workers = len(self._worker_pool)
            msg = f"{devices} device(s)  •  {orgs} organization(s)"
            if workers:
                msg += f"  •  {workers} operation(s) running"
            return msg

        def _refresh_status_bar_with_last(self, last: str | None = None) -> None:
            """Status bar includes base counts AND last operation (if any).

            When called without ``last``, reads from QSettings so the bar
            always reflects the persisted last-op even when the caller
            doesn't have an explicit value to display.
            """
            if last is None:
                settings = QSettings("ios-enroll", "gui")
                last = settings.value("lastOperation")
            base = self._status_bar_base_text()
            if last:
                base = f"{base}  •  Last: {last}"
            self.statusBar().showMessage(base)

        def _record_last_op(self, description: str) -> None:
            """Record an operation description to the status bar + QSettings.

            Called by long-running destructive operations (delete org,
            restore, enrollment, cache clear) so users returning to the
            app know what they were last doing — especially useful after
            a crash or accidental close during a long restore.

            Cheap: in-memory write + single QSettings.setValue call.
            """
            timestamp = datetime.now().strftime("%H:%M")
            entry = f"{description} @ {timestamp}"
            settings = QSettings("ios-enroll", "gui")
            settings.setValue("lastOperation", entry)
            self._refresh_status_bar_with_last(entry)

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


# Patch-propagation layer (Round 3 refactor).
#
# Tests patch names via "apple_device_cli.gui_qt.<name>" (the package
# namespace) and expect the patched value to be used inside this module.
# After the refactor split gui_qt.py into a package, this module
# <name> and gui_qt.<name> are separate bindings. Wrap the patchable
# names so they look up gui_qt.<name> at call time. Function names
# get a thin wrapper; class names get a subclass with a metaclass
# that proxies attribute access (so monkeypatch on the original
# class still propagates to instances via MRO).

_PATCHABLE_FUNCTIONS = (
    "list_devices",
    "get_device_info",
    "ensure_device_pairing",
    "activate_device",
    "make_supervised",
    "get_device_enrollment_state",
    "erase_device_for_reenrollment",
    "validate_enrollment_prerequisites",
    "resolve_skip_panes",
    "set_org_wifi",
    "resolve_cache_dir",
    "enter_recovery_mode",
    "exit_recovery_mode",
    "run_gui",
)

_PATCHABLE_CLASSES = (
    "OrganizationManager",
    "QFileDialog",
)


def _make_function_delegate(name, original):
    """Wrap a function so it looks up gui_qt.<name> at call time.

    Breaks the circular reference from gui_qt <init>.py
    'from .app import *': after that import, gui_qt.<name> IS this
    wrapper. Calling it would recurse forever, so when current is
    the wrapper itself, fall back to the original.
    """
    def wrapper(*args, **kwargs):
        from apple_device_cli import gui_qt
        current = getattr(gui_qt, name)
        if current is wrapper:
            return original(*args, **kwargs)
        return current(*args, **kwargs)
    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__module__ = __name__
    wrapper.__wrapped__ = original
    return wrapper


def _make_class_delegate(name, original):
    """Wrap a class so instantiation and attribute access look up gui_qt.<name>.

    Returns a subclass (_Delegate) of the original. _Delegate instances
    inherit __init__/methods from the original, so monkeypatch on the
    original class (e.g., 'OrganizationManager.__init__') propagates via
    MRO. When gui_qt.<name> is patched to a mock class, both the
    metaclass __getattr__ and __new__ forward to the mock.
    """
    class _DelegateMeta(type):
        def __getattr__(cls, attr):
            from apple_device_cli import gui_qt
            current = getattr(gui_qt, name, None)
            if current is None or current is _Delegate:
                return getattr(original, attr)
            return getattr(current, attr)

    class _Delegate(original, metaclass=_DelegateMeta):
        def __new__(cls, *args, **kwargs):
            from apple_device_cli import gui_qt
            current = getattr(gui_qt, name, None)
            if current is None or current is _Delegate:
                return super().__new__(cls)
            return current(*args, **kwargs)

    _Delegate.__name__ = name
    _Delegate.__qualname__ = name
    return _Delegate


for _name in _PATCHABLE_FUNCTIONS:
    _obj = globals().get(_name)
    if callable(_obj) and not isinstance(_obj, type):
        globals()[_name] = _make_function_delegate(_name, _obj)


for _name in _PATCHABLE_CLASSES:
    _obj = globals().get(_name)
    if isinstance(_obj, type):
        globals()[_name] = _make_class_delegate(_name, _obj)


del _name, _obj, _make_function_delegate, _make_class_delegate
del _PATCHABLE_FUNCTIONS, _PATCHABLE_CLASSES


if __name__ == "__main__":
    raise SystemExit(_main())
