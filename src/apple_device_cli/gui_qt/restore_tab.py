"""RestoreTab — controller for the Restore tab of the GUI.

Extracted from ``EnrollmentApp`` in Round 3 of the GUI refactor. Owns
the restore tab widgets (device/firmware/actions groupboxes, progress
bar, activity log) plus the log helpers (``_log_to_restore``,
``_append_restore_log``) and ``_clear_cache``. Most action handlers
(refresh versions, browse IPSW, start restore, recovery buttons,
etc.) remain on ``EnrollmentApp`` for now and reference widgets
through ``self.restore_tab_controller.<widget>``; they'll move into
this controller in a follow-up step.

Back-compat shims on ``EnrollmentApp`` (set in ``app.py``) forward
attribute reads for the restore widgets (``restore_device_combo``,
``restore_cache_path_label``, ``restore_clear_cache_btn``,
``restore_versions_combo``, ``restore_refresh_versions_btn``,
``restore_ipsw_path_label``, ``restore_verify_btn``,
``restore_start_btn``, ``restore_refresh_devices_btn``,
``restore_enter_recovery_btn``, ``restore_exit_recovery_btn``,
``restore_exit_recovery_any_btn``, ``restore_progress_bar``,
``restore_empty_state_label``, ``restore_log_text``,
``restore_product_type_label``, ``restore_device_mode_label``) to
this controller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apple_device_cli.restore.cache import cache_state, resolve_cache_dir

if TYPE_CHECKING:
    from apple_device_cli.gui_qt.app import EnrollmentApp


def _resolve_cache_dir():
    """Resolve through the package each call (tests patch)."""
    from apple_device_cli import gui_qt

    return gui_qt.resolve_cache_dir()


class RestoreTab:
    """Owns the Restore tab widgets, log helpers, and clear-cache action."""

    def __init__(self, shell: "EnrollmentApp") -> None:
        self._shell = shell
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
            self._shell._on_restore_device_changed
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
        self.restore_cache_path_label = QLabel(str(_resolve_cache_dir()))
        self.restore_cache_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        cache_row.addWidget(self.restore_cache_path_label, 1)
        cache_folder_btn = QPushButton("Change...")
        cache_folder_btn.clicked.connect(self._shell._pick_cache_folder)
        cache_row.addWidget(cache_folder_btn)
        show_cache_btn = QPushButton("Show cache")
        show_cache_btn.clicked.connect(self._shell._show_cache)
        cache_row.addWidget(show_cache_btn)
        self.restore_clear_cache_btn = QPushButton("Clear cache")
        self.restore_clear_cache_btn.clicked.connect(self._clear_restore_cache)
        cache_row.addWidget(self.restore_clear_cache_btn)
        firmware_layout.addRow("Cache:", cache_row)

        version_row = QHBoxLayout()
        self.restore_versions_combo = QComboBox()
        version_row.addWidget(self.restore_versions_combo, 1)
        self.restore_refresh_versions_btn = QPushButton("Refresh versions")
        self.restore_refresh_versions_btn.clicked.connect(
            self._shell._refresh_versions
        )
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
        browse_ipsw_btn.clicked.connect(self._shell._browse_ipsw)
        ipsw_row.addWidget(browse_ipsw_btn)
        self.restore_verify_btn = QPushButton("Verify (ipsw.me)")
        self.restore_verify_btn.clicked.connect(self._shell._verify_ipsw)
        self.restore_verify_btn.setEnabled(False)
        ipsw_row.addWidget(self.restore_verify_btn)
        firmware_layout.addRow("IPSW:", ipsw_row)

        setup_layout.addWidget(firmware_box)

        # ----- Actions row -----
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.restore_start_btn = QPushButton("Start Restore")
        self.restore_start_btn.setObjectName("restore_start_btn")
        f = self.restore_start_btn.font()
        f.setBold(True)
        self.restore_start_btn.setFont(f)
        self.restore_start_btn.setMinimumHeight(36)
        self.restore_start_btn.clicked.connect(self._shell._start_restore)
        self.restore_start_btn.setEnabled(False)
        actions_row.addWidget(self.restore_start_btn)

        actions_row.addSpacing(16)

        self.restore_refresh_devices_btn = QPushButton("Refresh Devices")
        self.restore_refresh_devices_btn.clicked.connect(
            self._shell._refresh_devices
        )
        actions_row.addWidget(self.restore_refresh_devices_btn)

        self.restore_enter_recovery_btn = QPushButton("Enter Recovery")
        self.restore_enter_recovery_btn.clicked.connect(
            self._shell._enter_recovery
        )
        self.restore_enter_recovery_btn.setEnabled(False)
        actions_row.addWidget(self.restore_enter_recovery_btn)

        self.restore_exit_recovery_btn = QPushButton("Exit Recovery")
        self.restore_exit_recovery_btn.clicked.connect(
            self._shell._exit_recovery
        )
        self.restore_exit_recovery_btn.setEnabled(False)
        actions_row.addWidget(self.restore_exit_recovery_btn)

        self.restore_exit_recovery_any_btn = QPushButton("Exit Recovery (any)")
        self.restore_exit_recovery_any_btn.clicked.connect(
            self._shell._exit_recovery_any
        )
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
        self.restore_empty_state_label = QLabel(
            "Select a device, then either browse for a local IPSW "
            "or refresh signed versions."
        )
        self.restore_empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.restore_empty_state_label.setWordWrap(True)
        self.restore_empty_state_label.setStyleSheet(
            "color: palette(mid); font-size: 13px; padding: 12px;"
            "border: 1px dashed palette(midlight); border-radius: 4px;"
        )
        status_layout.addWidget(self.restore_empty_state_label)
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
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([460, 280])
        splitter.setChildrenCollapsible(False)

        return outer

    # -- Logging helpers (moved from EnrollmentApp) -----------------------

    def _log_to_restore(self, message: str) -> None:
        """Append ``message`` to the Restore tab's log panel.

        Direct append (vs the signal-on-EnrollmentApp variant) — same
        effect on the test sync-worker path, simpler wiring.
        """
        self.restore_log_text.append(message)

    def _append_log(self, message: str) -> None:
        self.restore_log_text.append(message)

    # -- Clear cache ------------------------------------------------------

    def _clear_restore_cache(self) -> None:
        """Wipe the firmware cache after a confirmation prompt."""
        from PySide6.QtWidgets import QMessageBox
        import shutil

        cache_dir = _resolve_cache_dir()
        reply = QMessageBox.question(
            self._shell,
            "Clear cache?",
            f"Wipe firmware cache at {cache_dir}?\n\n"
            "All downloaded IPSW files will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._shell._log_to_restore(f"Cleared cache: {cache_dir}")
        except OSError as exc:
            QMessageBox.warning(
                self._shell,
                "Clear failed",
                f"Failed to clear cache: {exc}",
            )
            self._shell._log_to_restore(f"Clear cache failed: {exc}")