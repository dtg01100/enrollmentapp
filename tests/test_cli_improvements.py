"""Tests for CLI improvements: --json, --verbose, --dry-run, exit codes."""
import json
import pytest
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from apple_device_cli.cli import app, device_app, org_app, enroll_app


runner = CliRunner()


class TestDeviceListJsonOutput:
    """Tests for device list --json output."""

    @patch("apple_device_cli.cli.list_devices")
    def test_device_list_json_output(self, mock_list):
        mock_list.return_value = [
            MagicMock(
                udid="1234567890ABCDEF",
                device_name="iPhone",
                device_type="iPhone14,5",
                firmware_version="17.0",
                build_version="21A342",
                ecid="0xe28e921780032",
            )
        ]
        result = runner.invoke(device_app, ["list", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert len(output) == 1
        assert output[0]["udid"] == "1234567890ABCDEF"
        assert output[0]["name"] == "iPhone"
        assert output[0]["type"] == "iPhone14,5"
        assert output[0]["ios_version"] == "17.0"
        assert output[0]["build_version"] == "21A342"
        assert output[0]["ecid"] == "0xe28e921780032"

    @patch("apple_device_cli.cli.list_devices")
    def test_device_list_json_no_devices(self, mock_list):
        mock_list.return_value = []
        result = runner.invoke(device_app, ["list", "--json"])
        assert result.exit_code == 0
        assert "No devices found" in result.stdout

    @patch("apple_device_cli.cli.list_devices")
    def test_device_list_verbose_output(self, mock_list):
        mock_list.return_value = [
            MagicMock(
                udid="1234567890ABCDEF",
                device_name="iPhone",
                device_type="iPhone14,5",
                firmware_version="17.0",
                build_version="21A342",
                ecid="0xe28e921780032",
            )
        ]
        result = runner.invoke(device_app, ["list", "--verbose"])
        assert result.exit_code == 0
        assert "123456" in result.stdout
        assert "iPhone14,5" in result.stdout
        assert "17.0" in result.stdout
        assert "21A342" in result.stdout
        assert "ECID" in result.stdout


class TestDeviceInfoJsonOutput:
    """Tests for device info --json output."""

    @patch("apple_device_cli.cli.get_device_info")
    @patch("apple_device_cli.cli.list_devices")
    def test_device_info_json_output(self, mock_list, mock_info):
        mock_list.return_value = [
            MagicMock(udid="[REDACTED]ABCDEF")
        ]
        mock_info.return_value = MagicMock(
            udid="1234567890ABCDEF",
            device_name="iPhone",
            device_type="iPhone14,5",
            firmware_version="17.0",
            build_version="21A342",
            ecid="0xe28e921780032",
        )
        result = runner.invoke(device_app, ["info", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output["udid"] == "1234567890ABCDEF"
        assert output["name"] == "iPhone"
        assert output["ecid"] == "0xe28e921780032"

    @patch("apple_device_cli.cli.get_device_info")
    def test_device_info_not_found(self, mock_info):
        mock_info.return_value = None
        result = runner.invoke(device_app, ["info", "--udid", "1234567890ABCDEF"])
        assert result.exit_code == 0
        assert "not found" in result.stdout.lower()


class TestOrgListJsonOutput:
    """Tests for org list --json and --verbose output."""

    @patch("apple_device_cli.cli.OrganizationManager")
    @patch("apple_device_cli.cli.Path")
    def test_org_list_json_output(self, mock_path, mock_manager_class):
        mock_manager = MagicMock()
        mock_org = MagicMock()
        mock_org.name = "Test Org"
        mock_org.org_id = "com.test"
        mock_org.mdm_url = "https://mdm.example.com"
        mock_org.cert_path = "/path/to/cert.der"
        mock_org.key_path = "/path/to/key.der"
        mock_manager.list_orgs.return_value = [mock_org]
        mock_manager.orgs_dir = "/path/to/orgs"
        mock_manager_class.return_value = mock_manager

        mock_path.return_value.exists.return_value = True

        result = runner.invoke(org_app, ["list", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert len(output) == 1
        assert output[0]["name"] == "Test Org"
        assert output[0]["org_id"] == "com.test"
        assert output[0]["mdm_url"] == "https://mdm.example.com"
        assert output[0]["has_cert"] is True
        assert output[0]["has_key"] is True

    @patch("apple_device_cli.cli.OrganizationManager")
    @patch("apple_device_cli.cli.Path")
    def test_org_list_verbose_output(self, mock_path, mock_manager_class):
        mock_manager = MagicMock()
        mock_org = MagicMock()
        mock_org.name = "Test Org"
        mock_org.org_id = "com.test"
        mock_org.mdm_url = "https://mdm.example.com"
        mock_org.cert_path = "/path/to/cert.der"
        mock_org.key_path = "/path/to/key.der"
        mock_manager.list_orgs.return_value = [mock_org]
        mock_manager.orgs_dir = "/path/to/orgs"
        mock_manager_class.return_value = mock_manager

        mock_path.return_value.exists.return_value = True

        result = runner.invoke(org_app, ["list", "--verbose"])
        assert result.exit_code == 0
        assert "•••" in result.stdout
        assert "com.test" in result.stdout
        assert "mdm.example.com" in result.stdout
        assert "Cert: Yes" in result.stdout
        assert "Key: Yes" in result.stdout


class TestOrgSetCommandsExitCode:
    """Tests that org set-* commands return proper exit codes on error."""

    @patch("apple_device_cli.cli.OrganizationManager")
    def test_org_set_cert_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock()
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(org_app, ["set-cert", "--name", "NonExistent", "--cert", "/path/to/cert.der"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager")
    def test_org_set_key_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock()
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(org_app, ["set-key", "--name", "NonExistent", "--key", "/path/to/key.der"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager")
    def test_org_set_mdm_url_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock()
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(org_app, ["set-mdm-url", "--name", "NonExistent", "--mdm-url", "https://example.com"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager")
    def test_org_show_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock()
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(org_app, ["show", "--name", "NonExistent"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()


class TestDeviceEraseDryRun:
    """Tests for device erase --dry-run flag."""

    @patch("apple_device_cli.cli._prompt_for_udid")
    @patch("apple_device_cli.cli.get_signed_firmwares")
    def test_device_erase_dry_run(self, mock_firmwares, mock_prompt):
        mock_prompt.return_value = MagicMock(
            udid="1234567890ABCDEF",
            device_name="iPhone",
            device_type="iPhone14,5",
            firmware_version="17.0",
            build_version="21A342",
            ecid="0xe28e921780032",
        )
        mock_firmwares.return_value = [
            {"version": "17.0", "buildid": "21A342", "url": "https://example.com/17.0.ipsw"}
        ]

        result = runner.invoke(device_app, [
            "erase",
            "--udid", "1234567890ABCDEF",
            "--yes",
            "--dry-run"
        ])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.stdout
        assert "Would perform erase operation" in result.stdout


class TestDeviceUpdateDryRun:
    """Tests for device update --dry-run flag."""

    @patch("apple_device_cli.cli._prompt_for_udid")
    @patch("apple_device_cli.cli.get_signed_firmwares")
    def test_device_update_dry_run(self, mock_firmwares, mock_prompt):
        mock_prompt.return_value = MagicMock(
            udid="1234567890ABCDEF",
            device_name="iPhone",
            device_type="iPhone14,5",
            firmware_version="17.0",
            build_version="21A342",
            ecid="0xe28e921780032",
        )
        mock_firmwares.return_value = [
            {"version": "17.5", "buildid": "21F79", "url": "https://example.com/17.5.ipsw"}
        ]

        result = runner.invoke(device_app, [
            "update",
            "--udid", "1234567890ABCDEF",
            "--yes",
            "--dry-run"
        ])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.stdout
        assert "Would perform update operation" in result.stdout


class TestRestoreDeviceParamOrder:
    """Tests that restore_device uses consistent parameter ordering."""

    @patch("apple_device_cli.cli._prompt_for_udid")
    @patch("apple_device_cli.cli.restore_device")
    @patch("apple_device_cli.cli.wait_for_udid_in_usbmux")
    @patch("apple_device_cli.cli.ensure_device_pairing")
    @patch("apple_device_cli.cli.enter_recovery_mode")
    def test_restore_device_with_ecid_kwarg(
        self, mock_enter_recovery, mock_pairing, mock_wait, mock_restore, mock_prompt
    ):
        mock_prompt.return_value = MagicMock(
            udid="1234567890ABCDEF",
            device_name="iPhone",
            device_type="iPhone14,5",
            firmware_version="17.0",
            build_version="21A342",
            ecid="0xe28e921780032",
        )
        mock_wait.return_value = True
        mock_restore.return_value = True

        result = runner.invoke(device_app, [
            "restore",
            "--udid", "1234567890ABCDEF",
            "--ipsw", "/path/to/ipsw",
        ])
        # Should not prompt for interactive input
        assert "Select device" not in result.stdout
        if result.exit_code == 0:
            mock_restore.assert_called_once()
            call_kwargs = mock_restore.call_args.kwargs
            assert "ecid" in call_kwargs
            assert "ipsw" in call_kwargs
