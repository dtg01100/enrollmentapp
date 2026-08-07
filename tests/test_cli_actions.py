"""Tests for the pure business-logic functions in cli_actions.

These functions are extracted from the Typer command wrappers in cli.py so
the business logic can be tested without going through CliRunner.

Each test class exercises one extracted function:
    * happy path (calls the manager with the right args)
    * each failure path (raises the typed exception)

All class-shaped mocks are spec'd against the real class (AGENTS.md rule).
"""
from __future__ import annotations

import plistlib
from unittest.mock import MagicMock, patch

import pytest

from apple_device_cli.cli_actions import (
    OrgAlreadyExistsError,
    OrgNotFoundError,
    create_org,
    delete_org,
    generate_org,
    import_mobileconfig,
    import_org,
    set_org_field,
    set_org_wifi,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_manager():
    """Return a spec'd OrganizationManager ready for spec'd attribute access."""
    from apple_device_cli.orgs.manager import OrganizationManager

    return MagicMock(spec=OrganizationManager)


def _build_org(name="Test", **overrides):
    """Return a spec'd Organization with the fields commands read."""
    from apple_device_cli.orgs.manager import Organization

    org = MagicMock(spec=Organization)
    org.name = name
    org.org_id = None
    org.address = None
    org.phone = None
    org.email = None
    org.mdm_url = None
    org.checkin_url = None
    org.mdm_topic = None
    org.mdm_description = None
    org.cert_path = None
    org.key_path = None
    org.wifi_config_path = None
    org.mdm_mobileconfig_path = None
    org.identity_ref = None
    org.created_at = "2024-01-01T00:00:00"
    for key, value in overrides.items():
        setattr(org, key, value)
    return org


# ---------------------------------------------------------------------------
# set_org_field
# ---------------------------------------------------------------------------


class TestSetOrgField:
    """set_org_field — common body for org set-{cert,key,mdm-url,...} commands."""

    def test_happy_path_sets_attribute_and_saves(self):
        manager = _build_manager()
        org = _build_org(name="Acme")
        manager.get_org.return_value = org

        set_org_field(manager, "Acme", "mdm_url", "https://x.example.com/mdm", "MDM URL")

        manager.get_org.assert_called_once_with("Acme")
        assert org.mdm_url == "https://x.example.com/mdm"
        manager.save_org.assert_called_once()
        # save_org is called with the org object and overwrite=True
        args, kwargs = manager.save_org.call_args
        assert args[0] is org
        assert kwargs.get("overwrite") is True

    def test_not_found_raises_org_not_found(self):
        manager = _build_manager()
        manager.get_org.return_value = None

        with pytest.raises(OrgNotFoundError) as exc_info:
            set_org_field(manager, "Ghost", "mdm_url", "https://x", "MDM URL")

        assert exc_info.value.name == "Ghost"
        manager.save_org.assert_not_called()

    def test_propagates_save_org_value_error(self):
        """If save_org raises ValueError (e.g. concurrent lock collision), the
        caller sees it and decides how to format the user-facing message."""
        manager = _build_manager()
        org = _build_org(name="Acme")
        manager.get_org.return_value = org
        manager.save_org.side_effect = ValueError("save failed")

        with pytest.raises(ValueError, match="save failed"):
            set_org_field(manager, "Acme", "mdm_url", "https://x", "MDM URL")


# ---------------------------------------------------------------------------
# create_org
# ---------------------------------------------------------------------------


class TestCreateOrg:
    """create_org — builds an Organization and saves it via the manager."""

    def test_happy_path_returns_create_org_result(self):
        manager = _build_manager()

        result = create_org(
            manager=manager,
            name="Acme",
            org_id="com.acme",
            address="123 Main St",
            phone="+1-555-1212",
            email="ops@acme.example",
            mdm_url="https://mdm.acme.example/mdm",
            checkin_url="https://mdm.acme.example/checkin",
            mdm_topic="com.acme.mdm",
            mdm_description="Acme MDM",
            cert="/tmp/cert.der",
            key="/tmp/key.der",
            wifi_config=None,
        )

        manager.save_org.assert_called_once()
        saved_org = manager.save_org.call_args.args[0]
        assert saved_org.name == "Acme"
        assert saved_org.org_id == "com.acme"
        assert saved_org.mdm_url == "https://mdm.acme.example/mdm"
        # The result exposes the canonical fields the caller needs to display.
        assert result.name == "Acme"
        assert result.mdm_url == "https://mdm.acme.example/mdm"
        assert result.checkin_url == "https://mdm.acme.example/checkin"
        assert result.mdm_topic == "com.acme.mdm"

    def test_omit_optional_paths(self):
        """When cert/key/wifi_config are None, those fields are left None."""
        manager = _build_manager()

        create_org(
            manager=manager,
            name="Acme",
            org_id=None,
            address=None,
            phone=None,
            email=None,
            mdm_url=None,
            checkin_url=None,
            mdm_topic=None,
            mdm_description=None,
            cert=None,
            key=None,
            wifi_config=None,
        )

        saved_org = manager.save_org.call_args.args[0]
        assert saved_org.cert_path is None
        assert saved_org.key_path is None
        assert saved_org.wifi_config_path is None

    def test_wifi_config_path_is_set_when_provided(self, tmp_path):
        """When wifi_config is a real path, the org's wifi_config_path is populated."""
        manager = _build_manager()
        wifi = tmp_path / "wifi.mobileconfig"
        wifi.write_bytes(b"")  # content doesn't matter for the field-set path

        create_org(
            manager=manager,
            name="Acme",
            org_id=None,
            address=None,
            phone=None,
            email=None,
            mdm_url=None,
            checkin_url=None,
            mdm_topic=None,
            mdm_description=None,
            cert=None,
            key=None,
            wifi_config=str(wifi),
        )

        saved_org = manager.save_org.call_args.args[0]
        assert saved_org.wifi_config_path == str(wifi.resolve())

    def test_manager_none_falls_back_to_default(self, monkeypatch, tmp_path):
        """When manager=None, _resolve_manager instantiates OrganizationManager().

        The test patches OrganizationManager at the import location so the
        real constructor (which reads Path.home()/.config/...) never runs
        and the fallback branch at cli_actions.py:118-119 is exercised.
        """
        from apple_device_cli.cli_actions import OrganizationManager
        from apple_device_cli.orgs import manager as mgr_mod

        sentinel = MagicMock(spec=OrganizationManager)
        monkeypatch.setattr(mgr_mod, "OrganizationManager", lambda: sentinel)
        # Also point DEFAULT_ORGS_DIR at tmp_path so the real save_org path
        # (called via the real OrganizationManager.save) doesn't pollute
        # ~/.config.
        monkeypatch.setattr(mgr_mod, "DEFAULT_ORGS_DIR", tmp_path)

        result = create_org(
            manager=None,
            name="Acme",
            org_id=None,
            address=None,
            phone=None,
            email=None,
            mdm_url=None,
            checkin_url=None,
            mdm_topic=None,
            mdm_description=None,
            cert=None,
            key=None,
            wifi_config=None,
        )

        assert result.name == "Acme"

    def test_existing_org_raises_org_already_exists(self):
        manager = _build_manager()
        manager.save_org.side_effect = ValueError("Organization 'Acme' already exists")

        with pytest.raises(OrgAlreadyExistsError) as exc_info:
            create_org(
                manager=manager,
                name="Acme",
                org_id=None,
                address=None,
                phone=None,
                email=None,
                mdm_url=None,
                checkin_url=None,
                mdm_topic=None,
                mdm_description=None,
                cert=None,
                key=None,
                wifi_config=None,
            )

        assert "Acme" in str(exc_info.value)

    def test_unexpected_value_error_propagates(self):
        """Non-already-exists ValueError from save_org bubbles up verbatim."""
        manager = _build_manager()
        manager.save_org.side_effect = ValueError("lock contention")

        with pytest.raises(ValueError, match="lock contention"):
            create_org(
                manager=manager,
                name="Acme",
                org_id=None,
                address=None,
                phone=None,
                email=None,
                mdm_url=None,
                checkin_url=None,
                mdm_topic=None,
                mdm_description=None,
                cert=None,
                key=None,
                wifi_config=None,
            )


# ---------------------------------------------------------------------------
# delete_org
# ---------------------------------------------------------------------------


class TestDeleteOrg:
    """delete_org — wrapper around manager.delete_org with a typed exception."""

    def test_happy_path_returns_true(self):
        manager = _build_manager()
        manager.delete_org.return_value = True

        result = delete_org(manager, "Acme")

        assert result is True
        manager.delete_org.assert_called_once_with("Acme")

    def test_not_found_raises_org_not_found(self):
        manager = _build_manager()
        manager.delete_org.return_value = False

        with pytest.raises(OrgNotFoundError) as exc_info:
            delete_org(manager, "Ghost")

        assert exc_info.value.name == "Ghost"


# ---------------------------------------------------------------------------
# import_org
# ---------------------------------------------------------------------------


class TestImportOrg:
    """import_org — wrapper around manager.import_org."""

    def test_happy_path_returns_org(self):
        manager = _build_manager()
        manager.import_org.return_value = _build_org(name="Imported")

        result = import_org(manager, "/tmp/foo.organization", password="")

        # Empty password string is replaced with the default "password".
        manager.import_org.assert_called_once_with("/tmp/foo.organization", "password")
        assert result.name == "Imported"

    def test_password_passed_through(self):
        manager = _build_manager()
        manager.import_org.return_value = _build_org(name="Imported")

        import_org(manager, "/tmp/foo.organization", password="secret")

        manager.import_org.assert_called_once_with("/tmp/foo.organization", "secret")

    def test_invalid_path_raises_value_error(self):
        manager = _build_manager()
        manager.import_org.side_effect = ValueError("Invalid path: /tmp/x")

        with pytest.raises(ValueError, match="Invalid path"):
            import_org(manager, "/tmp/x")

    def test_wrong_password_raises_value_error(self):
        manager = _build_manager()
        manager.import_org.side_effect = ValueError("Failed to decode identity (wrong password?)")

        with pytest.raises(ValueError, match="wrong password"):
            import_org(manager, "/tmp/foo.organization", password="bad")


# ---------------------------------------------------------------------------
# import_mobileconfig
# ---------------------------------------------------------------------------


class TestImportMobileconfig:
    """import_mobileconfig — wrapper around manager.import_mobileconfig."""

    def test_happy_path_returns_org(self):
        manager = _build_manager()
        manager.import_mobileconfig.return_value = _build_org(
            name="MC", mdm_url="https://mdm.example.com/mdm",
        )

        result = import_mobileconfig(manager, "/tmp/foo.mobileconfig")

        manager.import_mobileconfig.assert_called_once_with("/tmp/foo.mobileconfig")
        assert result.name == "MC"
        assert result.mdm_url == "https://mdm.example.com/mdm"

    def test_missing_file_raises_value_error(self):
        manager = _build_manager()
        manager.import_mobileconfig.side_effect = ValueError("File not found: /tmp/x")

        with pytest.raises(ValueError, match="File not found"):
            import_mobileconfig(manager, "/tmp/x")

    def test_already_exists_raises_value_error(self):
        manager = _build_manager()
        manager.import_mobileconfig.side_effect = ValueError("Organization 'X' already exists")

        with pytest.raises(ValueError, match="already exists"):
            import_mobileconfig(manager, "/tmp/x.mobileconfig")


# ---------------------------------------------------------------------------
# set_org_wifi
# ---------------------------------------------------------------------------


class TestSetOrgWifi:
    """set_org_wifi — attach a WiFi mobileconfig to an existing org."""

    def test_happy_path_copies_file_and_saves(self, tmp_path):
        manager = _build_manager()
        org = _build_org(name="Acme")
        manager.get_org.return_value = org
        manager._sanitize_name = lambda n: "Acme"
        manager.orgs_dir = tmp_path

        # Real Apple plist (empty PayloadContent is valid).
        wifi_path = tmp_path / "in.mobileconfig"
        wifi_path.write_bytes(plistlib.dumps({"PayloadContent": []}))

        # Pre-create the org's destination directory so the copy works.
        org_dir = tmp_path / "Acme"
        org_dir.mkdir()

        result = set_org_wifi(manager, "Acme", str(wifi_path))

        assert result.name == "Acme"
        assert result.wifi_config_path == str(org_dir / "wifi.mobileconfig")
        # The actual file was copied.
        assert (org_dir / "wifi.mobileconfig").exists()
        # skip_copy=True is critical — wifi was already copied via shutil.copy
        # above; org.save() must NOT re-copy or the test setup's pre-created
        # org_dir would conflict with the save()'s copy logic.
        org.save.assert_called_once_with(org_dir, skip_copy=True)
        # The org's wifi_config_path was updated to the dest.
        assert org.wifi_config_path == str(org_dir / "wifi.mobileconfig")

    def test_org_not_found_raises(self, tmp_path):
        manager = _build_manager()
        manager.get_org.return_value = None

        wifi_path = tmp_path / "in.mobileconfig"
        wifi_path.write_bytes(plistlib.dumps({"PayloadContent": []}))

        with pytest.raises(OrgNotFoundError) as exc_info:
            set_org_wifi(manager, "Ghost", str(wifi_path))

        assert exc_info.value.name == "Ghost"

    def test_wifi_file_missing_raises(self, tmp_path):
        manager = _build_manager()
        org = _build_org(name="Acme")
        manager.get_org.return_value = org

        missing = tmp_path / "missing.mobileconfig"
        assert not missing.exists()

        from apple_device_cli.cli_actions import WifiConfigNotFoundError

        with pytest.raises(WifiConfigNotFoundError) as exc_info:
            set_org_wifi(manager, "Acme", str(missing))

        assert exc_info.value.path == str(missing)

    def test_invalid_plist_raises(self, tmp_path):
        manager = _build_manager()
        org = _build_org(name="Acme")
        manager.get_org.return_value = org

        bad_path = tmp_path / "bad.mobileconfig"
        bad_path.write_bytes(b"not a plist")

        from apple_device_cli.cli_actions import WifiConfigInvalidError

        with pytest.raises(WifiConfigInvalidError) as exc_info:
            set_org_wifi(manager, "Acme", str(bad_path))

        assert exc_info.value.path == str(bad_path)


# ---------------------------------------------------------------------------
# generate_org
# ---------------------------------------------------------------------------


class TestGenerateOrg:
    """generate_org — call identity gen, write cert/key, save org."""

    @patch("apple_device_cli.cli_actions.generate_org_identity")
    def test_happy_path_creates_files_and_saves(self, mock_gen_id, tmp_path):
        manager = _build_manager()
        manager.orgs_dir = tmp_path
        manager._sanitize_name = lambda n: "Acme"
        manager.get_org.return_value = None
        mock_gen_id.return_value = (b"cert-bytes", b"key-bytes")

        result = generate_org(
            manager=manager,
            name="Acme",
            org_id="com.acme",
            mdm_url="https://mdm.acme.example/mdm",
            checkin_url="https://mdm.acme.example/checkin",
            mdm_topic="com.acme.mdm",
            mdm_description="Acme",
            valid_days=365,
        )

        mock_gen_id.assert_called_once_with("Acme", 365)
        # Files were written.
        org_dir = tmp_path / "Acme"
        assert (org_dir / "cert.der").read_bytes() == b"cert-bytes"
        assert (org_dir / "key.der").read_bytes() == b"key-bytes"
        # The result is a typed container.
        assert result.name == "Acme"
        assert result.org_id == "com.acme"
        assert result.mdm_url == "https://mdm.acme.example/mdm"
        assert result.checkin_url == "https://mdm.acme.example/checkin"
        assert result.mdm_topic == "com.acme.mdm"
        assert result.mdm_description == "Acme"
        assert result.cert_path == str(org_dir / "cert.der")
        assert result.key_path == str(org_dir / "key.der")

    @patch("apple_device_cli.cli_actions.generate_org_identity")
    def test_existing_org_dir_is_replaced(self, mock_gen_id, tmp_path):
        manager = _build_manager()
        manager.orgs_dir = tmp_path
        manager._sanitize_name = lambda n: "Acme"
        manager.get_org.return_value = None
        mock_gen_id.return_value = (b"cert", b"key")

        # Pre-existing org directory with a stale file.
        org_dir = tmp_path / "Acme"
        org_dir.mkdir()
        (org_dir / "stale").write_text("old")

        generate_org(
            manager=manager,
            name="Acme",
            org_id=None,
            mdm_url=None,
            checkin_url=None,
            mdm_topic=None,
            mdm_description=None,
            valid_days=365,
        )

        # The stale file was removed and the new files are present.
        assert not (org_dir / "stale").exists()
        assert (org_dir / "cert.der").exists()
        assert (org_dir / "key.der").exists()
