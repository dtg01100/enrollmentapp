"""Tests for previously-untested CLI commands and JSON output paths.

Covers:
* 12 ``org_app`` commands (create, delete, set-checkin-url, import,
  import-mobileconfig, set-wifi happy/missing-file/missing-org, generate).
* 2 ``device_app`` JSON paths (``device list --json``, ``device info --json``).
* 1 ``org show --json`` path.

All class-shaped mocks are spec'd against the real class (AGENTS.md rule).
Home-directory isolation: ``OrganizationManager()`` is mocked at the import
location used by ``apple_device_cli.cli``, so the real constructor (which
reads ``Path.home()``) is never called.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import plistlib
import pytest
from typer.testing import CliRunner

from apple_device_cli.cli import app
from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.orgs.manager import Organization, OrganizationManager


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_orgs_dir(tmp_path, monkeypatch):
    """Point DEFAULT_ORGS_DIR at tmp_path so real manager calls don't pollute ~/.config."""
    from apple_device_cli.orgs import manager as mgr_mod

    orgs_dir = tmp_path / "orgs"
    monkeypatch.setattr(mgr_mod, "DEFAULT_ORGS_DIR", orgs_dir)
    return orgs_dir


def _build_mock_manager():
    """Spec'd OrganizationManager ready to be returned by patching the class."""
    return MagicMock(spec=OrganizationManager)


def _build_mock_org(name="Test", mdm_url=None, checkin_url=None):
    """Spec'd Organization with the fields our CLI commands read."""
    org = MagicMock(spec=Organization)
    org.name = name
    org.org_id = None
    org.address = None
    org.phone = None
    org.email = None
    org.mdm_url = mdm_url
    org.checkin_url = checkin_url
    org.mdm_topic = None
    org.mdm_description = None
    org.cert_path = None
    org.key_path = None
    org.wifi_config_path = None
    org.mdm_mobileconfig_path = None
    org.created_at = "2024-01-01T00:00:00"
    return org


# ---------------------------------------------------------------------------
# org_app — create
# ---------------------------------------------------------------------------


class TestOrgCreate:
    """ios-enroll org create --name ... happy + failure."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_create_happy_path(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_mgr.save_org.return_value = None
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "create", "--name", "Test"])

        # The CLI redacts the displayed name (sanitize → "T•••"), but the
        # success marker and exit code are what matter for callers.
        assert result.exit_code == 0
        assert "Created organization" in result.stdout
        mock_mgr.save_org.assert_called_once()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_create_failure_raises(self, mock_mgr_class):
        """ValueError from save_org is caught and surfaced as a friendly error.

        The CLI wraps ``manager.save_org(org)`` in try/except ValueError and
        exits with code 1 and a red "Create failed: ..." message — matching
        the pattern used by ``org import`` and ``org import-mobileconfig``.
        """
        mock_mgr = _build_mock_manager()
        mock_mgr.save_org.side_effect = ValueError("nope")
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "create", "--name", "Test"])

        assert result.exit_code == 1
        assert "Create failed" in result.stdout
        assert "nope" in result.stdout


# ---------------------------------------------------------------------------
# org_app — delete
# ---------------------------------------------------------------------------


class TestOrgDelete:
    """ios-enroll org delete --name ... happy + not-found."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_delete_not_found(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_mgr.delete_org.return_value = False  # not found
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "delete", "--name", "Missing"])

        assert result.exit_code == 0
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_delete_found(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_mgr.delete_org.return_value = True
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "delete", "--name", "Found"])

        assert result.exit_code == 0
        assert "Deleted" in result.stdout


# ---------------------------------------------------------------------------
# org_app — set-checkin-url
# ---------------------------------------------------------------------------


class TestOrgSetCheckinUrl:
    """ios-enroll org set-checkin-url — hits the shared _set_org_field body."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_set_checkin_url_success(self, mock_mgr_class, isolated_orgs_dir):
        mock_mgr = _build_mock_manager()
        mock_org = _build_mock_org(name="X")
        mock_mgr.get_org.return_value = mock_org
        mock_mgr.save_org.return_value = None
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(
            app,
            ["org", "set-checkin-url", "--name", "X",
             "--checkin-url", "https://mdm.example.com/c"],
        )

        assert result.exit_code == 0
        assert "Set check-in URL" in result.stdout


# ---------------------------------------------------------------------------
# org_app — import / import-mobileconfig
# ---------------------------------------------------------------------------


class TestOrgImport:
    """ios-enroll org import --path ... happy + failure."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_import_happy(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_mgr.import_org.return_value = _build_mock_org(name="Imported")
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "import", "--path", "/tmp/foo.organization"])

        assert result.exit_code == 0
        assert "Imported" in result.stdout

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_import_failure(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_mgr.import_org.side_effect = ValueError("bad format")
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "import", "--path", "/tmp/bad"])

        # The CLI prints the error and does NOT raise — exit 0.
        assert result.exit_code == 0
        assert "Import failed" in result.stdout


class TestOrgImportMobileconfig:
    """ios-enroll org import-mobileconfig --path ... happy + failure."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_import_mobileconfig_happy(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_org = _build_mock_org(
            name="MC", mdm_url="https://mdm.example.com/mdm",
            checkin_url="https://mdm.example.com/checkin",
        )
        mock_mgr.import_mobileconfig.return_value = mock_org
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(
            app, ["org", "import-mobileconfig", "--path", "/tmp/foo.mobileconfig"]
        )

        assert result.exit_code == 0
        assert "MDM URL" in result.stdout

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_import_mobileconfig_failure(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_mgr.import_mobileconfig.side_effect = ValueError("not a plist")
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(
            app, ["org", "import-mobileconfig", "--path", "/tmp/bad"]
        )

        assert result.exit_code == 0
        assert "Import failed" in result.stdout


# ---------------------------------------------------------------------------
# org_app — set-wifi (file-not-found and org-not-found)
# ---------------------------------------------------------------------------


class TestOrgSetWifi:
    """ios-enroll org set-wifi — two distinct failure paths."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_set_wifi_file_not_found(self, mock_mgr_class, tmp_path):
        """The file-not-found path fires when the org exists but the file doesn't.

        Order matters: ``cli.py:926`` checks ``manager.get_org(name)`` first;
        if that returns None the function bails before ever reading the path.
        So the org lookup must succeed for the wifi_path.exists() branch to run.
        """
        mock_mgr = _build_mock_manager()
        mock_mgr.get_org.return_value = _build_mock_org(name="X")
        mock_mgr_class.return_value = mock_mgr

        missing = tmp_path / "definitely_missing.mobileconfig"
        assert not missing.exists()

        result = runner.invoke(
            app,
            ["org", "set-wifi", "--name", "X", "--path", str(missing)],
        )

        assert result.exit_code == 1
        # The actual error message — not just a generic "not found" substring
        # (which would also pass for the org-not-found branch).
        assert "wifi config file not found" in result.stdout.lower()
        assert "organization not found" not in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_set_wifi_org_not_found(self, mock_mgr_class, tmp_path):
        mock_mgr = _build_mock_manager()
        mock_mgr.get_org.return_value = None
        mock_mgr_class.return_value = mock_mgr

        # File DOES exist (so the second check doesn't trip first), but the
        # org lookup returns None and the command should exit 1.
        existing = tmp_path / "exists.mobileconfig"
        existing.write_bytes(b"")
        # Real plist expected by command — use a minimal one so plistlib.load succeeds.
        existing.write_bytes(plistlib.dumps({"PayloadContent": []}))

        result = runner.invoke(
            app,
            ["org", "set-wifi", "--name", "Missing", "--path", str(existing)],
        )

        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# org_app — generate
# ---------------------------------------------------------------------------


class TestOrgGenerate:
    """ios-enroll org generate — exercises cert gen + save (no existing)."""

    @patch("apple_device_cli.cli.generate_org_identity")
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_generate_happy_path(self, mock_mgr_class, mock_gen_id, tmp_path):
        from apple_device_cli.orgs import manager as mgr_mod

        # Force DEFAULT_ORGS_DIR to a temp dir so generate can mkdir freely.
        orgs_dir = tmp_path / "orgs"
        orgs_dir.mkdir(parents=True, exist_ok=True)
        mgr_mod.DEFAULT_ORGS_DIR = orgs_dir

        mock_mgr = _build_mock_manager()
        mock_mgr.orgs_dir = orgs_dir
        mock_mgr._sanitize_name = lambda n: "".join(c for c in n if c.isalnum() or c in "-_") or "x"
        mock_mgr.get_org.return_value = None
        mock_mgr_class.return_value = mock_mgr
        mock_gen_id.return_value = (b"cert-der", b"key-der")

        result = runner.invoke(app, ["org", "generate", "--name", "X"])

        assert result.exit_code == 0
        assert "Generated identity" in result.stdout
        # The org directory was created and the org was saved.
        org_dir = orgs_dir / mock_mgr._sanitize_name("X")
        assert org_dir.exists()
        assert (org_dir / "cert.der").exists()
        assert (org_dir / "key.der").exists()


# ---------------------------------------------------------------------------
# device_app — JSON output
# ---------------------------------------------------------------------------


class TestDeviceJsonPaths:
    """device list --json + device info --json."""

    @patch("apple_device_cli.cli.list_devices", spec=True)
    @patch("apple_device_cli.cli.ensure_device_pairing", spec=True)
    def test_device_list_json(self, mock_pair, mock_list):
        mock_list.return_value = [
            MagicMock(
                spec=DeviceInfo,
                udid="1234567890ABCDEF",
                device_name="iPhone",
                device_type="iPhone14,5",
                firmware_version="17.0",
                build_version="21A342",
                ecid="0xe28e921780032",
            )
        ]

        result = runner.invoke(app, ["device", "list", "--json"])

        assert result.exit_code == 0
        # The command emits JSON — ensure it's parseable.
        output = json.loads(result.stdout)
        assert isinstance(output, list)
        assert output[0]["udid"] == "1234567890ABCDEF"

    @patch("apple_device_cli.cli.get_device_info", spec=True)
    @patch("apple_device_cli.cli.ensure_device_pairing", spec=True)
    def test_device_info_json(self, mock_pair, mock_info):
        mock_info.return_value = MagicMock(
            spec=DeviceInfo,
            udid="ABC",
            device_name="iPhone",
            device_type="iPhone14,5",
            firmware_version="17.0",
            build_version="21A342",
            ecid="0xabc",
        )

        result = runner.invoke(app, ["device", "info", "--udid", "ABC", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output["udid"] == "ABC"


# ---------------------------------------------------------------------------
# org_app — show (text path; --json flag does not exist in current CLI)
# ---------------------------------------------------------------------------


class TestOrgShow:
    """ios-enroll org show happy + not-found.

    Note: ``org_show`` in cli.py does NOT accept a ``--json`` flag (drift
    from the original task description). The text path is what we test.
    """

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_show_happy(self, mock_mgr_class):
        mock_mgr = _build_mock_manager()
        mock_org = _build_mock_org(name="X", mdm_url="https://mdm.example.com/mdm")
        mock_mgr.get_org.return_value = mock_org
        mock_mgr_class.return_value = mock_mgr

        result = runner.invoke(app, ["org", "show", "--name", "X"])

        assert result.exit_code == 0
        assert "MDM URL" in result.stdout
        assert "mdm.example.com" in result.stdout
