"""OrgsTab — controller for the Organizations tab of the GUI.

Extracted from ``EnrollmentApp`` in Round 3 of the GUI refactor. Owns
the orgs toolbar (Refresh / Create / Generate Identity / Edit / Import /
Export / Attach WiFi / Delete), the org list, the read-only details
pane, and the action handlers. Cross-tab updates (re-populating the
Enrollment tab's org combo, refreshing the device list after a delete)
are dispatched through the ``shell`` reference.

Back-compat shims on ``EnrollmentApp`` (set in ``app.py``) forward
attribute reads for the orgs widgets (``orgs_list``,
``refresh_orgs_btn``, ``create_org_btn``, ``generate_id_btn``,
``edit_org_btn``, ``import_org_btn``, ``export_org_btn``,
``attach_wifi_btn``, ``delete_org_btn``, ``orgs_details_label``,
``orgs_empty_label``) to this controller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apple_device_cli.cli_actions import (
    OrgNotFoundError,
    WifiConfigInvalidError,
    WifiConfigNotFoundError,
)
from apple_device_cli.orgs.identity import generate_org_identity
from apple_device_cli.orgs.manager import Organization


def _organization_manager():
    """Resolve OrganizationManager through the package each call (tests patch)."""
    from apple_device_cli import gui_qt

    return gui_qt.OrganizationManager()


def _set_org_wifi(manager, name, path):
    """Resolve set_org_wifi through the package each call (tests patch)."""
    from apple_device_cli import gui_qt

    return gui_qt.set_org_wifi(manager, name, path)

if TYPE_CHECKING:
    from apple_device_cli.gui_qt.app import EnrollmentApp


def _cert_expiry(path):
    """Resolve ``_cert_expiry`` through the package each call (tests patch)."""
    from apple_device_cli import gui_qt

    return gui_qt._cert_expiry(path)


def _format_cert_expiry_badge(days):
    from apple_device_cli import gui_qt

    return gui_qt._format_cert_expiry_badge(days)


def _write_identity_atomic(org_dir, cert, key):
    from apple_device_cli import gui_qt

    return gui_qt._write_identity_atomic(org_dir, cert, key)


def validate_org_fields(name, mdm_url=None, checkin_url=None, mdm_topic=None):
    from apple_device_cli import gui_qt

    return gui_qt.validate_org_fields(name, mdm_url, checkin_url, mdm_topic)


def validate_identity_days(days):
    from apple_device_cli import gui_qt

    return gui_qt.validate_identity_days(days)


class OrgsTab:
    """Owns the Orgs tab widgets and action logic."""

    def __init__(self, shell: "EnrollmentApp") -> None:
        self._shell = shell
        # Per-tab OrganizationManager — removes the implicit coupling
        # where one shared manager served all tabs.
        self._manager = _organization_manager()
        self.widget = self._build()

    # -- TabController protocol ------------------------------------------

    def tab_widget(self) -> QWidget:
        return self.widget

    def refresh(self) -> None:
        self._refresh()

    def on_org_changed(self, org: Any) -> None:
        return None

    def on_device_changed(self, device: Any) -> None:
        return None

    # -- Build -----------------------------------------------------------

    def _build(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.refresh_orgs_btn = QPushButton("Refresh Orgs")
        self.refresh_orgs_btn.clicked.connect(self._refresh)
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
        self.orgs_list.currentRowChanged.connect(self._shell._update_org_details)

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

    # -- Selection -------------------------------------------------------

    def _selected_org(self) -> Organization | None:
        current = self.orgs_list.currentRow()
        if current < 0 or current >= len(self._shell._orgs):
            return None
        return self._shell._orgs[current]

    # -- Refresh + completion -------------------------------------------

    def _refresh(self) -> None:
        self._shell._log("Refreshing organizations...")
        token = self._shell._next_token()
        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(lambda: self._manager.list_orgs())
        self._shell._run_worker(
            worker, self._on_refreshed, [self.refresh_orgs_btn], token=token
        )

    @Slot(object, object)
    def _on_refreshed(self, result: Any, error: Exception | None, token: int) -> None:
        if not self._shell._is_current_token(token):
            return
        if error:
            self._shell._log(f"Failed to list organizations: {error}")
            return
        orgs = result or []
        self._shell._orgs = list(orgs)
        self.orgs_list.clear()
        for org in self._shell._orgs:
            has_identity = bool(org.cert_path and org.key_path)
            badge = (
                _format_cert_expiry_badge(_cert_expiry(org.cert_path))
                if has_identity
                else ""
            )
            display = (
                f"{org.name}  (MDM: {org.mdm_url or 'none'}, "
                f"identity: {'yes' if has_identity else 'no'}"
                f"{badge})"
            )
            QListWidgetItem(display, self.orgs_list)
        self.orgs_empty_label.setVisible(self.orgs_list.count() == 0)
        if self._shell._orgs:
            self.orgs_list.setCurrentRow(0)
        self._shell._update_enroll_orgs()
        self._shell._log(f"Found {len(self._shell._orgs)} organization(s).")
        self._shell._update_status_bar()

    # -- Create / Generate Identity / Edit ------------------------------

    def _create_org_dialog(self) -> None:
        from apple_device_cli.gui_qt.app import OrgValidationError

        dialog = QDialog(self._shell)
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

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
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
                self._manager.save_org(org)
                self._shell._log(f"Created organization: {name}")
                dialog.accept()
                self._refresh()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(dialog, "Save failed", f"Failed to create organization: {exc}")
                self._shell._log(f"Failed to create organization: {exc}")

        button_box.accepted.connect(save_org)
        button_box.rejected.connect(dialog.reject)
        dialog.exec()

    def _generate_identity_dialog(self) -> None:
        org = self._selected_org()
        if not org:
            QMessageBox.warning(self._shell, "No organization", "Select an organization first.")
            return

        dialog = QDialog(self._shell)
        dialog.setWindowTitle(f"Generate Identity for {org.name}")
        dialog.setModal(True)
        layout = QFormLayout(dialog)

        days_edit = QLineEdit(str(365 * 5))
        layout.addRow("Validity (days):", days_edit)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
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

            from apple_device_cli.gui_qt.app import WorkerThread

            worker = WorkerThread(work)
            self._shell._run_worker(worker, on_done, [self.generate_id_btn])

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
            self._shell._log(f"Identity generation failed: {error}")
            QMessageBox.warning(dialog, "Generation failed", f"Identity generation failed: {error}")
            return
        if not result or not isinstance(result, tuple) or len(result) != 2:
            self._shell._log("Identity generation returned an unexpected result.")
            return
        cert_der, key_der = result
        manager = self._manager
        org_dir = manager.org_dir_for(org.name)
        try:
            _write_identity_atomic(org_dir, cert_der, key_der)
        except OSError as exc:
            self._shell._log(f"Failed to write identity: {exc}")
            QMessageBox.warning(dialog, "Write failed", f"Failed to write identity: {exc}")
            return
        org.cert_path = str(org_dir / "cert.der")
        org.key_path = str(org_dir / "key.der")
        manager.save_org(org, overwrite=True)
        self._shell._log(f"Generated identity for {org.name}")
        dialog.accept()
        self._refresh()

    def _delete_org(self) -> None:
        org = self._selected_org()
        if not org:
            QMessageBox.warning(self._shell, "No organization", "Select an organization first.")
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
            self._shell,
            "Confirm Delete",
            warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._manager.delete_org(org.name)
            self._shell._log(f"Deleted organization: {org.name}")
            self._shell._record_last_op(f"Deleted org '{org.name}'")
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self._shell, "Delete failed", f"Failed to delete organization: {exc}")
            self._shell._log(f"Failed to delete organization: {exc}")

    def _edit_org(self) -> None:
        """Entry point for the 'Edit Org' button."""
        org = self._selected_org()
        if not org:
            QMessageBox.warning(self._shell, "No organization", "Select an organization first.")
            return
        dialog, fields = self._build_edit_org_form(org)
        button_box = dialog.findChild(QDialogButtonBox)
        assert button_box is not None

        def save() -> None:
            self._apply_edit_org(org, fields, dialog)

        button_box.accepted.connect(save)
        button_box.rejected.connect(dialog.reject)
        dialog.exec()

    def _import_org(self) -> None:
        """Entry point for the 'Import…' button."""
        path_str, _ = QFileDialog.getOpenFileName(
            self._shell,
            "Import organization",
            "",
            "All supported (*.organization *.mobileconfig);;"
            "Apple Configurator (*.organization);;"
            "Mobileconfig (*.mobileconfig);;"
            "All Files (*)",
        )
        if not path_str:
            return
        from pathlib import Path

        path = Path(path_str)
        manager = self._manager

        def work() -> Organization:
            if path.suffix.lower() == ".mobileconfig":
                return manager.import_mobileconfig(path)
            return manager.import_org(path)

        def on_done(result: Organization, error: Exception | None) -> None:
            if error:
                QMessageBox.warning(self._shell, "Import failed", f"Failed to import: {error}")
                self._shell._log(f"Import failed: {error}")
                return
            self._shell._log(f"Imported organization: {result.name}")
            self._refresh()

        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(work)
        self._shell._run_worker(worker, on_done, [self.import_org_btn])

    def _export_org(self) -> None:
        """Entry point for the 'Export…' button."""
        org = self._selected_org()
        if not org:
            QMessageBox.warning(self._shell, "No organization", "Select an organization first.")
            return
        default_name = f"{org.name}.zip"
        path_str, _ = QFileDialog.getSaveFileName(
            self._shell,
            "Export organization",
            default_name,
            "Zip (*.zip);;Directory (use a folder name)",
        )
        if not path_str:
            return
        from pathlib import Path

        dest = Path(path_str)

        def work() -> bool:
            return self._manager.export_org(org.name, dest)

        def on_done(result: bool, error: Exception | None) -> None:
            if error:
                QMessageBox.warning(self._shell, "Export failed", f"Failed to export: {error}")
                self._shell._log(f"Export failed: {error}")
                return
            if not result:
                QMessageBox.warning(self._shell, "Export failed", "export_org returned False")
                return
            self._shell._log(f"Exported organization: {org.name} → {dest}")
            self._shell._record_last_op(f"Exported org '{org.name}'")
            QMessageBox.information(
                self._shell, "Export complete", f"Exported to {dest}"
            )

        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(work)
        self._shell._run_worker(worker, on_done, [self.export_org_btn])

    def _attach_wifi(self) -> None:
        """Entry point for the 'Attach WiFi…' button."""
        org = self._selected_org()
        if not org:
            QMessageBox.warning(self._shell, "No organization", "Select an organization first.")
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self._shell,
            "Choose WiFi mobileconfig",
            "",
            "Mobileconfig (*.mobileconfig);;All Files (*)",
        )
        if not path_str:
            return
        from pathlib import Path

        wifi_path = Path(path_str)
        if org.wifi_config_path:
            reply = QMessageBox.question(
                self._shell,
                "Replace WiFi config?",
                (
                    f"Replace existing WiFi config on '{org.name}'?\n\n"
                    f"Old: {org.wifi_config_path}\nNew: {wifi_path}"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        manager = self._manager

        def work() -> Any:
            return _set_org_wifi(manager, org.name, str(wifi_path))

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
                QMessageBox.warning(self._shell, "Attach failed", msg)
                self._shell._log(f"Attach WiFi failed: {error}")
                return
            self._shell._log(f"Attached WiFi to '{org.name}': {result.wifi_config_path}")
            self._refresh()

        from apple_device_cli.gui_qt.app import WorkerThread

        worker = WorkerThread(work)
        self._shell._run_worker(worker, on_done, [self.attach_wifi_btn])

    def _build_edit_org_form(self, org: Organization) -> tuple[QDialog, dict[str, QLineEdit]]:
        """Construct the Edit Org dialog."""
        dialog = QDialog(self._shell)
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
                dialog, "Choose certificate (DER)", "", "DER (*.der);;All Files (*)"
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
        """Validate form, build a fresh Organization, and save it."""
        from apple_device_cli.gui_qt.app import OrgValidationError

        name = org.name
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
            key_path=org.key_path,
            wifi_config_path=org.wifi_config_path,
        )
        try:
            self._manager.save_org(updated, overwrite=True)
            self._shell._log(f"Updated organization: {name}")
            self._shell._record_last_op(f"Updated org '{name}'")
            dialog.accept()
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(dialog, "Save failed", f"Failed to update: {exc}")
            self._shell._log(f"Failed to update organization: {exc}")