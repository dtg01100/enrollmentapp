"""Tests for CLI improvements: --json, --verbose, --dry-run, exit codes."""

import json
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from apple_device_cli.cli import device_app, org_app
from apple_device_cli.core.exceptions import AppleDeviceError
from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.orgs.manager import Organization, OrganizationManager


runner = CliRunner()


class TestDeviceListJsonOutput:
    """Tests for device list --json output."""

    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_device_list_json_output(self, mock_list):
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

    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_device_list_json_no_devices(self, mock_list):
        """--json with no devices must emit valid JSON ([]), not prose."""
        mock_list.return_value = []
        result = runner.invoke(device_app, ["list", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_device_list_json_error_emits_error_object(self, mock_list):
        """--json on failure emits a parseable error object, not prose."""
        mock_list.side_effect = AppleDeviceError("usbmuxd not reachable")
        result = runner.invoke(device_app, ["list", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output == {"error": "usbmuxd not reachable"}

    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_device_list_verbose_output(self, mock_list):
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
        result = runner.invoke(device_app, ["list", "--verbose"])
        assert result.exit_code == 0
        assert "123456" in result.stdout
        assert "iPhone14,5" in result.stdout
        assert "17.0" in result.stdout
        assert "21A342" in result.stdout
        assert "ECID" in result.stdout


class TestDeviceInfoJsonOutput:
    """Tests for device info --json output."""

    @patch("apple_device_cli.cli.get_device_info", spec=True)
    def test_device_info_json_output(self, mock_info):
        mock_info.return_value = MagicMock(
            spec=DeviceInfo,
            udid="1234567890ABCDEF",
            device_name="iPhone",
            device_type="iPhone14,5",
            firmware_version="17.0",
            build_version="21A342",
            ecid="0xe28e921780032",
        )
        result = runner.invoke(device_app, ["info", "--udid", "1234567890ABCDEF", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output["udid"] == "1234567890ABCDEF"
        assert output["name"] == "iPhone"
        assert output["ecid"] == "0xe28e921780032"

    @patch("apple_device_cli.cli.get_device_info", spec=True)
    def test_device_info_not_found(self, mock_info):
        mock_info.return_value = None
        result = runner.invoke(device_app, ["info", "--udid", "1234567890ABCDEF"])
        assert result.exit_code == 0
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.get_device_info", spec=True)
    @patch("apple_device_cli.cli.ensure_device_pairing", spec=True)
    def test_device_info_json_not_found_emits_error_object(
        self, mock_pair, mock_info
    ):
        """--json on an unknown device emits a parseable error object."""
        mock_info.return_value = None
        result = runner.invoke(
            device_app, ["info", "--udid", "1234567890ABCDEF", "--json"]
        )
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert "error" in output
        assert "not found" in output["error"].lower()

    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_device_info_json_requires_udid(self, mock_list):
        """--json can't drive the interactive picker — require --udid."""
        result = runner.invoke(device_app, ["info", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.stdout)
        assert output["error"] == "--udid is required with --json"
        mock_list.assert_not_called()


class TestOrgListJsonOutput:
    """Tests for org list --json and --verbose output."""

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.Path", spec=True)
    def test_org_list_json_output(self, mock_path, mock_manager_class):
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_org = MagicMock(spec=Organization)
        mock_org.name = "Test Org"
        mock_org.org_id = "com.test"
        mock_org.mdm_url = "https://mdm.example.com"
        mock_org.checkin_url = "https://mdm.example.com/checkin"
        mock_org.mdm_topic = "com.test.topic"
        mock_org.cert_path = "/path/to/cert.der"
        mock_org.key_path = "/path/to/key.der"
        mock_org.wifi_config_path = None
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
        assert output[0]["checkin_url"] == "https://mdm.example.com/checkin"
        assert output[0]["mdm_topic"] == "com.test.topic"
        assert output[0]["has_cert"] is True
        assert output[0]["has_key"] is True
        assert output[0]["wifi_config_path"] is None

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_list_json_empty_outputs_array(self, mock_manager_class):
        """--json with no orgs must still emit valid JSON ([]), not prose."""
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_manager.list_orgs.return_value = []
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(org_app, ["list", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.Path", spec=True)
    def test_org_list_verbose_output(self, mock_path, mock_manager_class):
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_org = MagicMock(spec=Organization)
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

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_set_cert_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(
            org_app, ["set-cert", "--name", "NonExistent", "--cert", "/path/to/cert.der"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_set_key_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(
            org_app, ["set-key", "--name", "NonExistent", "--key", "/path/to/key.der"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_set_mdm_url_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(
            org_app, ["set-mdm-url", "--name", "NonExistent", "--mdm-url", "https://example.com"]
        )
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_show_not_found_returns_error(self, mock_manager_class):
        mock_manager = MagicMock(spec=OrganizationManager)
        mock_manager.get_org.return_value = None
        mock_manager_class.return_value = mock_manager

        result = runner.invoke(org_app, ["show", "--name", "NonExistent"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower()
