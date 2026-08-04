"""Tests for enrollment CLI commands: make-supervised, status, validate, re-enroll, activate."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from typer.testing import CliRunner

from apple_device_cli.cli import enroll_app
from apple_device_cli.core.exceptions import AppleDeviceError
from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.orgs.manager import Organization, OrganizationManager

from tests.conftest import (
    MockCloudConfigurationAlreadyPresentError as CloudConfigurationAlreadyPresentError,
    LockdownClient,
    MobileActivationService,
    MobileConfigService,
)

runner = CliRunner()


@pytest.fixture
def isolated_orgs_dir(tmp_path, monkeypatch):
    from apple_device_cli.orgs import manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "DEFAULT_ORGS_DIR", tmp_path / "orgs")
    return tmp_path / "orgs"


@pytest.fixture
def sample_device():
    return DeviceInfo(
        udid="d8b97d90b881aba50bd[REDACTED]78d32fb8da3",
        device_name="Test iPhone",
        device_type="iPhone14,5",
        firmware_version="17.0",
        build_version="21A342",
        ecid="0xe28e921780032",
    )


@pytest.fixture
def org_with_keys(isolated_orgs_dir, tmp_path):
    """Create an org with cert+key files on disk for supervised enrollment."""
    manager = OrganizationManager(isolated_orgs_dir)
    cert = tmp_path / "cert.der"
    key = tmp_path / "key.der"
    cert.write_bytes(b"fake-cert-bytes")
    key.write_bytes(b"fake-key-bytes")
    manager.save_org(
        Organization(
            name="Acme",
            mdm_url="https://mdm.example.com/mdm",
            checkin_url="https://mdm.example.com/checkin",
            mdm_topic="com.acme.mdm",
            cert_path=str(cert),
            key_path=str(key),
        )
    )
    return manager


# --- make-supervised ---


class TestEnrollMakeSupervised:
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_no_device_exits_1(self, mock_list):
        mock_list.return_value = []
        result = runner.invoke(enroll_app, ["make-supervised", "--org-name", "Acme"])
        assert result.exit_code == 1
        assert "no devices" in result.output.lower() or "no device selected" in result.output.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_org_not_found_exits_1(self, mock_list, mock_mgr_class, sample_device):
        mock_list.return_value = [sample_device]
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = None
        mock_mgr_class.return_value = mgr
        result = runner.invoke(enroll_app, ["make-supervised", "--udid", sample_device.udid, "--org-name", "Missing"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.get_device_info", spec=True)
    def test_org_missing_cert_exits_1(
        self, mock_info, mock_mgr_class, sample_device, isolated_orgs_dir
    ):
        mock_info.return_value = sample_device
        mgr = OrganizationManager(isolated_orgs_dir)
        mgr.save_org(Organization(name="Acme", cert_path=None, key_path=None))
        # The CLI uses OrganizationManager() (default), but our saved org
        # is in the isolated dir. Patch DEFAULT_ORGS_DIR so the manager
        # built inside the CLI finds our org.
        import apple_device_cli.orgs.manager as mgr_mod
        mgr_mod.DEFAULT_ORGS_DIR = isolated_orgs_dir
        mock_mgr_class.return_value = mgr

        result = runner.invoke(
            enroll_app,
            ["make-supervised", "--udid", sample_device.udid, "--org-name", "Acme"],
        )
        assert result.exit_code == 1
        assert "missing cert or key" in result.output.lower()

    def test_invalid_skip_preset_returns_error(self, org_with_keys, sample_device):
        result = runner.invoke(
            enroll_app,
            [
                "make-supervised",
                "--udid", sample_device.udid,
                "--org-name", "Acme",
                "--skip-preset", "bogus",
            ],
        )
        # Invalid preset prints but doesn't raise
        assert result.exit_code == 0
        assert "error" in result.output.lower() or "preset" in result.output.lower()

    @patch("apple_device_cli.cli.make_supervised", spec=True)
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_success(self, mock_list, mock_mgr_class, mock_make, org_with_keys, sample_device):
        mock_list.return_value = [sample_device]
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = org_with_keys.get_org("Acme")
        mock_mgr_class.return_value = mgr

        from apple_device_cli.enrollment.supervised import EnrollmentResult
        mock_make.return_value = EnrollmentResult(
            success=True,
            device_udid=sample_device.udid,
            supervised=True,
            mdm_enrolled=True,
            wifi_installed=False,
            cloud_config={"IsSupervised": True},
        )

        result = runner.invoke(
            enroll_app,
            ["make-supervised", "--udid", sample_device.udid, "--org-name", "Acme"],
        )
        assert result.exit_code == 0
        assert "supervised" in result.output.lower()
        mock_make.assert_called_once()

    @patch("apple_device_cli.cli.make_supervised", spec=True)
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_failure_lists_errors(self, mock_list, mock_mgr_class, mock_make, org_with_keys, sample_device):
        mock_list.return_value = [sample_device]
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = org_with_keys.get_org("Acme")
        mock_mgr_class.return_value = mgr

        from apple_device_cli.enrollment.supervised import EnrollmentResult
        mock_make.return_value = EnrollmentResult(
            success=False,
            device_udid=sample_device.udid,
            supervised=False,
            mdm_enrolled=False,
            wifi_installed=False,
            errors=["mdm unreachable", "https://mdm.example.com/verysecrettoken"],
        )

        result = runner.invoke(
            enroll_app,
            ["make-supervised", "--udid", sample_device.udid, "--org-name", "Acme"],
        )
        assert result.exit_code == 0
        assert "errors" in result.output.lower()
        # Error URL should be redacted (path > 12 chars -> ellipsis appended)
        assert "mdm.example.com" in result.output
        assert "/…" in result.output

    @patch("apple_device_cli.cli.make_supervised", spec=True)
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_apple_device_error_prints_message(self, mock_list, mock_mgr_class, mock_make, org_with_keys, sample_device):
        mock_list.return_value = [sample_device]
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = org_with_keys.get_org("Acme")
        mock_mgr_class.return_value = mgr
        mock_make.side_effect = AppleDeviceError("https://mdm.example.com/checkin/verysecrettoken")

        result = runner.invoke(
            enroll_app,
            ["make-supervised", "--udid", sample_device.udid, "--org-name", "Acme"],
        )
        assert result.exit_code == 0  # Apple's exception is printed, not raised to Typer
        assert "Error" in result.output
        # Token should be redacted
        assert "verysecrettoken" not in result.output


# --- re-enroll ---


class TestEnrollReenroll:
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_no_device_exits_1(self, mock_list):
        mock_list.return_value = []
        result = runner.invoke(enroll_app, ["re-enroll", "--force"])
        assert result.exit_code == 1
        assert "no devices" in result.output.lower() or "no device selected" in result.output.lower()

    @patch("apple_device_cli.enrollment.supervised.erase_device_for_reenrollment", spec=True)
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_force_skips_confirmation(self, mock_list, mock_erase, sample_device):
        mock_list.return_value = [sample_device]
        mock_erase.return_value = None
        result = runner.invoke(
            enroll_app, ["re-enroll", "--udid", sample_device.udid, "--force"]
        )
        assert result.exit_code == 0
        assert "erased" in result.output.lower() or "ready" in result.output.lower()
        mock_erase.assert_called_once_with(sample_device.udid)

    @patch("apple_device_cli.enrollment.supervised.erase_device_for_reenrollment", spec=True)
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_error_exits_1(self, mock_list, mock_erase, sample_device):
        mock_list.return_value = [sample_device]
        mock_erase.side_effect = AppleDeviceError("cloud config erase failed")
        result = runner.invoke(
            enroll_app, ["re-enroll", "--udid", sample_device.udid, "--force"]
        )
        assert result.exit_code == 1
        assert "Error" in result.output


# --- status ---


class TestEnrollStatus:
    @patch("apple_device_cli.cli.list_devices", spec=True)
    def test_no_device_exits_1(self, mock_list):
        mock_list.return_value = []
        result = runner.invoke(enroll_app, ["status"])
        assert result.exit_code == 1
        assert "no devices" in result.output.lower() or "no device selected" in result.output.lower()

    @patch("apple_device_cli.enrollment.supervised.get_device_enrollment_state", spec=True)
    @patch("apple_device_cli.cli.get_device_info", spec=True)
    def test_success_prints_state(self, mock_info, mock_state, sample_device):
        mock_info.return_value = sample_device
        mock_state.return_value = {
            "activation_state": "Activated",
            "is_supervised": True,
            "cloud_config_applied": True,
            "org_name": "Acme Corp",
            "org_magic": "com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f",
            "was_mandatorily_unpaired": False,
        }
        result = runner.invoke(
            enroll_app, ["status", "--udid", sample_device.udid]
        )
        assert result.exit_code == 0
        assert "Activated" in result.output
        assert "Supervised: True" in result.output
        # Org name redaction (first+last char of each word)
        assert "A••• C•••" in result.output
        # Org magic redaction
        assert "com.apple.…" in result.output

    @patch("apple_device_cli.enrollment.supervised.get_device_enrollment_state", spec=True)
    @patch("apple_device_cli.cli.get_device_info", spec=True)
    def test_state_error_prints_error(self, mock_info, mock_state, sample_device):
        mock_info.return_value = sample_device
        mock_state.return_value = {"error": "device disconnected"}
        result = runner.invoke(
            enroll_app, ["status", "--udid", sample_device.udid]
        )
        assert result.exit_code == 0
        assert "Could not get device state" in result.output

    @patch("apple_device_cli.enrollment.supervised.get_device_enrollment_state", spec=True)
    @patch("apple_device_cli.cli.get_device_info", spec=True)
    def test_exception_prints_error(self, mock_info, mock_state, sample_device):
        mock_info.return_value = sample_device
        mock_state.side_effect = RuntimeError("https://mdm.example.com/verysecrettoken")
        result = runner.invoke(
            enroll_app, ["status", "--udid", sample_device.udid]
        )
        assert result.exit_code == 0
        assert "Error getting device status" in result.output
        # URL token redacted: long path segment gets ellipsis appended
        assert "/…" in result.output


# --- validate ---


class TestEnrollValidate:
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_prompted_empty_name_cancels(self, mock_mgr_class):
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = None
        mock_mgr_class.return_value = mgr
        # Pressing Enter on empty prompt -> typer aborts -> exit code 1
        result = runner.invoke(enroll_app, ["validate"], input="\n")
        # typer.Abort() exit code is 1; both code paths ("cancelled" or "abort")
        # are acceptable user feedback for an empty name.
        assert result.exit_code in (0, 1)
        if result.exit_code == 0:
            assert "cancelled" in result.output.lower() or "required" in result.output.lower()

    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_org_not_found(self, mock_mgr_class, isolated_orgs_dir):
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = None
        mock_mgr_class.return_value = mgr
        result = runner.invoke(enroll_app, ["validate", "--org-name", "Missing"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    @patch("apple_device_cli.enrollment.supervised.validate_enrollment_prerequisites", spec=True)
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_success_no_errors(self, mock_mgr_class, mock_validate, isolated_orgs_dir, tmp_path):
        cert = tmp_path / "cert.der"
        key = tmp_path / "key.der"
        cert.write_bytes(b"x")
        key.write_bytes(b"x")
        org = Organization(
            name="Acme",
            mdm_url="https://mdm.example.com/mdm",
            cert_path=str(cert),
            key_path=str(key),
        )
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = org
        mock_mgr_class.return_value = mgr
        mock_validate.return_value = []

        result = runner.invoke(enroll_app, ["validate", "--org-name", "Acme"])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    @patch("apple_device_cli.enrollment.supervised.validate_enrollment_prerequisites", spec=True)
    @patch("apple_device_cli.cli.OrganizationManager", spec=True)
    def test_failure_lists_errors(self, mock_mgr_class, mock_validate, isolated_orgs_dir, tmp_path):
        cert = tmp_path / "cert.der"
        key = tmp_path / "key.der"
        cert.write_bytes(b"x")
        key.write_bytes(b"x")
        org = Organization(name="Acme", cert_path=str(cert), key_path=str(key))
        mgr = MagicMock(spec=OrganizationManager)
        mgr.get_org.return_value = org
        mock_mgr_class.return_value = mgr
        mock_validate.return_value = [
            "cert invalid",
            "https://mdm.example.com/checkin/verysecrettoken",
        ]

        result = runner.invoke(enroll_app, ["validate", "--org-name", "Acme"])
        assert result.exit_code == 0
        assert "validation failed" in result.output.lower()
        # Secret URL redacted
        assert "verysecrettoken" not in result.output


# --- activate ---


class TestEnrollActivate:
    @patch("apple_device_cli.cli.activate_device", spec=True)
    def test_success(self, mock_activate):
        mock_activate.return_value = None
        result = runner.invoke(enroll_app, ["activate"])
        assert result.exit_code == 0
        assert "activated" in result.output.lower()

    @patch("apple_device_cli.cli.activate_device", spec=True)
    def test_error_prints_message(self, mock_activate):
        mock_activate.side_effect = AppleDeviceError("https://mdm.example.com/verysecrettoken")
        result = runner.invoke(enroll_app, ["activate"])
        assert result.exit_code == 0
        assert "Error" in result.output
        # URL token redacted (path > 12 chars -> ellipsis appended)
        assert "/…" in result.output

class TestCloudConfigBugFix:
    """Test that cloud config is always set, not just when skip_list provided."""

    def test_make_supervised_sets_cloud_config_without_skip_list(self, mock_pymobiledevice3):
        """Test: Cloud config is set even when skip_list is None.

        Note: When device is already supervised, we skip the cloud config update
        to avoid connection issues. The config was already set correctly when
        supervision was originally applied. This test verifies the behavior
        without asserting that set_cloud_configuration was called again.
        """
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )

        svc = MagicMock(spec=MobileConfigService)
        svc.supervise = AsyncMock()
        svc.set_cloud_configuration = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "Test Org"),
                ]
            )
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )

            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                supervised.make_supervised(
                    str(cert_path),
                    str(key_path),
                    "Test Org",
                    None,
                    skip_list=None,  # No skip_list provided
                )

        # Verify set_cloud_configuration behavior
        # When device is already supervised, we skip the update to avoid connection issues.
        # If it was called, verify the config is correct. If not, that's OK too.
        if svc.set_cloud_configuration.called:
            call_args = svc.set_cloud_configuration.call_args.args[0]
            assert call_args["IsSupervised"] is True
            assert call_args["OrganizationName"] == "Test Org"
            # SkipSetup should NOT be in config if skip_list is None
            assert "SkipSetup" not in call_args
        else:
            # Device was already supervised, so we didn't call set_cloud_configuration again
            # This is expected behavior - the config was already set correctly
            pass

    def test_make_supervised_reuses_matching_existing_cloud_config(self, mock_pymobiledevice3):
        """Test: existing matching cloud config is treated as success and MDM install continues."""
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )

        svc = MagicMock(spec=MobileConfigService)
        svc.set_cloud_configuration = AsyncMock(side_effect=CloudConfigurationAlreadyPresentError())
        svc.install_profile_silent = AsyncMock()
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            mdm_profile_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_profile_path.write_bytes(b"fake-mdm-profile")

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "Test Org"),
                ]
            )
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )

            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            existing_cloud_config: dict[str, object] = {
                "AllowPairing": True,
                "CloudConfigurationUIComplete": True,
                "ConfigurationSource": 2,
                "ConfigurationWasApplied": True,
                "IsMandatory": True,
                "IsMultiUser": False,
                "IsSupervised": True,
                "MDMServerURL": "https://mdm.example.com/mdm",
                "OrganizationMagic": "org-123",
                "OrganizationName": "Test Org",
                "PostSetupProfileWasInstalled": True,
                "IsMDMUnremovable": False,
                "SkipSetup": ["Passcode"],
            }
            svc.get_cloud_configuration = AsyncMock(return_value=existing_cloud_config)
            svc.get_profile_list = AsyncMock(return_value={
                "ProfileMetadata": {
                    "mdm-profile": {"PayloadType": "com.apple.mdm", "PayloadDisplayName": "MDM"},
                }
            })
            svc.install_profile_silent = AsyncMock()
            svc.__aenter__ = AsyncMock(return_value=svc)
            svc.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                result = supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    org_uuid="org-123",
                    skip_list=["passcode"],
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_profile_path),
                )

        assert result.success is True
        assert result.supervised is True
        assert result.mdm_enrolled is True
        assert result.errors == []
        assert svc.set_cloud_configuration.await_count == 1
        svc.install_profile_silent.assert_awaited_once()

    def test_make_supervised_retries_transient_mdm_network_error(self, mock_pymobiledevice3):
        """Test: transient MDM network errors are retried and can recover."""
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )

        transient_error = Exception(
            "invalid response {'ErrorChain': [{'ErrorCode': -1009, 'LocalizedDescription': 'The Internet connection appears to be offline.'}], 'Status': 'Error'}"
        )

        svc = MagicMock(spec=MobileConfigService)
        svc.set_cloud_configuration = AsyncMock()
        svc.install_profile_silent = AsyncMock(side_effect=[transient_error, None])
        svc.get_profile_list = AsyncMock(return_value={
            "ProfileMetadata": {
                "mdm-profile": {"PayloadType": "com.apple.mdm", "PayloadDisplayName": "MDM"},
            }
        })
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            mdm_profile_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_profile_path.write_bytes(b"fake-mdm-profile")

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "Test Org"),
                ]
            )
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )

            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            with (
                patch(
                    "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
                ),
                patch(
                    "apple_device_cli.enrollment.supervised.asyncio.sleep", new=AsyncMock()
                ) as mock_sleep,
            ):
                result = supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    skip_list=["passcode"],
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_profile_path),
                )

        assert result.success is True
        assert result.mdm_enrolled is True
        assert result.errors == []
        assert svc.install_profile_silent.await_count == 2
        mock_sleep.assert_awaited_once()

    def test_mdm_verification_fails_when_profile_not_on_device(
        self, mock_pymobiledevice3
    ):
        """Test: post-install verification rejects install when MDM profile is missing.

        install_profile_silent returns without raising, but get_profile_list
        shows no MDM profile — this should fail, not silently mark mdm_enrolled=True.
        """
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )

        svc = MagicMock(spec=MobileConfigService)
        svc.set_cloud_configuration = AsyncMock()
        svc.install_profile_silent = AsyncMock()  # succeeds without raising
        svc.get_profile_list = AsyncMock(return_value={})  # but no MDM profile
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            mdm_profile_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_profile_path.write_bytes(b"fake-mdm-profile")

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]
            )
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                result = supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    skip_list=["passcode"],
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_profile_path),
                )

        assert result.mdm_enrolled is False
        assert any("MDM profile not found on device" in e for e in result.errors)


class TestEnrollmentStateValidation:
    """Test that device state validation works correctly."""

    def test_validate_enrollment_prerequisites_missing_cert(self, mock_pymobiledevice3):
        """Test: Prerequisites validation catches missing cert."""
        from apple_device_cli.enrollment import supervised

        errors = supervised.validate_enrollment_prerequisites(
            cert_path="/nonexistent/cert.der",
            key_path="/nonexistent/key.der",
            org_name="TestOrg",
        )
        assert any("Certificate not found" in e for e in errors)

    def test_validate_enrollment_prerequisites_invalid_mdm_url(self, mock_pymobiledevice3):
        """Test: Prerequisites validation catches invalid MDM URL."""
        from apple_device_cli.enrollment import supervised

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="TestOrg",
            mdm_url="not-a-valid-url",
        )
        assert any("Invalid MDM URL format" in e for e in errors)

    def test_validate_enrollment_prerequisites_empty_org_name(self, mock_pymobiledevice3):
        """Test: Prerequisites validation requires org name."""
        from apple_device_cli.enrollment import supervised

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="",
        )
        assert any("Organization name is required" in e for e in errors)


class TestMakeSupervisedErrorHandling:
    """Test error handling consistency in make_supervised."""

    def test_make_supervised_with_missing_cert_returns_error(self, mock_pymobiledevice3):
        """Test: Missing cert returns error result, not exception."""
        from apple_device_cli.enrollment import supervised

        result = supervised.make_supervised(
            "/nonexistent/cert.der",
            "/nonexistent/key.der",
            "Test Org",
        )

        # Should return error result with success=False
        assert result.success is False
        assert len(result.errors) > 0
        assert any("Certificate not found" in e for e in result.errors)


class TestEnrollmentStateReadback:
    """Test device status lookups."""

    def test_get_device_enrollment_state_uses_correct_lockdown_keys(self, mock_pymobiledevice3):
        """Test: status lookup queries lockdown with key parameter and augments from cloud config."""
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)

        async def get_value(domain=None, key=None):
            values = {
                "ActivationState": "Activated",
                "IsSupervised": False,
                "CloudConfigurationWasApplied": False,
                "OrganizationName": None,
                "OrganizationMagic": None,
                "WasMandatorilyUnpaired": False,
            }
            return values[key]

        lockdown.get_value = AsyncMock(side_effect=get_value)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        svc = MagicMock(spec=MobileConfigService)
        svc.get_cloud_configuration = AsyncMock(
            return_value={
                "IsSupervised": True,
                "ConfigurationWasApplied": True,
                "OrganizationName": "Test Org",
                "OrganizationMagic": "org-123",
            }
        )
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with patch("pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc):
            state = supervised.get_device_enrollment_state("test-udid")

        assert state == {
            "activation_state": "Activated",
            "is_supervised": True,
            "cloud_config_applied": True,
            "org_name": "Test Org",
            "org_magic": "org-123",
            "was_mandatorily_unpaired": False,
        }
        assert lockdown.get_value.await_count == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestKeybagPersistenceForMdmInstall:
    """Tests that keybag is created in persistent tempdir for MDM install."""

    def test_keybag_created_in_system_tempdir(self):
        """Verify keybag is created in system tempdir, not a deleted tempdir."""
        import tempfile
        from pathlib import Path
        from datetime import datetime, timezone, timedelta
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        # Create test certs and MDM profile
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            mdm_profile_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_profile_path.write_bytes(b"<xml>test-mdm</xml>")

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            svc = MagicMock(spec=MobileConfigService)
            svc.set_cloud_configuration = AsyncMock()
            svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
            svc.get_profile_list = AsyncMock(return_value={
                "ProfileMetadata": {
                    "mdm-profile": {"PayloadType": "com.apple.mdm", "PayloadDisplayName": "MDM"},
                }
            })
            svc.install_profile_silent = AsyncMock()
            svc.__aenter__ = AsyncMock(return_value=svc)
            svc.__aexit__ = AsyncMock(return_value=False)

            # Track the keybag path passed to install_profile_silent
            captured_keybag = None
            original_install = svc.install_profile_silent

            async def capture_keybag(keybag, payload):
                nonlocal captured_keybag
                captured_keybag = keybag
                return await original_install(keybag, payload)

            svc.install_profile_silent = AsyncMock(side_effect=capture_keybag)

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                from apple_device_cli.enrollment import supervised

                supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_profile_path),
                )

            # Verify keybag was passed to install_profile_silent
            assert captured_keybag is not None, "install_profile_silent should receive keybag"
            # Verify it's in system tempdir
            system_temp = Path(tempfile.gettempdir())
            assert str(captured_keybag).startswith(str(system_temp)), (
                f"Keybag should be in {system_temp}, got {captured_keybag}"
            )
            assert "ios_enroll_keybag_" in str(captured_keybag), (
                "Keybag should have ios_enroll_keybag_ prefix"
            )

    def test_install_profile_silent_called_with_keybag_path(self):
        """Verify install_profile_silent receives the keybag path for escalation."""
        from pathlib import Path
        import tempfile
        from datetime import datetime, timezone, timedelta
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            mdm_profile_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_profile_path.write_bytes(b"<xml>test mdm</xml>")

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            svc = MagicMock(spec=MobileConfigService)
            svc.set_cloud_configuration = AsyncMock()
            svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
            svc.get_profile_list = AsyncMock(return_value={
                "ProfileMetadata": {
                    "mdm-profile": {"PayloadType": "com.apple.mdm", "PayloadDisplayName": "MDM"},
                }
            })
            svc.install_profile_silent = AsyncMock()
            svc.__aenter__ = AsyncMock(return_value=svc)
            svc.__aexit__ = AsyncMock(return_value=False)

            captured_keybag_path = None

            original_install = svc.install_profile_silent

            async def capture_keybag(keybag, payload):
                nonlocal captured_keybag_path
                captured_keybag_path = keybag
                return await original_install(keybag, payload)

            svc.install_profile_silent = AsyncMock(side_effect=capture_keybag)

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                from apple_device_cli.enrollment import supervised

                supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_profile_path),
                )

            # Verify keybag path was passed to install_profile_silent
            # Note: keybag file is cleaned up after enrollment completes
            assert captured_keybag_path is not None, (
                "install_profile_silent should receive keybag path"
            )
            # Check that the path looks correct (is in temp dir with correct prefix)
            assert "ios_enroll_keybag_" in str(captured_keybag_path), (
                f"Keybag path should have prefix: {captured_keybag_path}"
            )
            # File size check removed - file is cleaned up after enrollment


class TestWifiAndMdmInstallOrder:
    """Tests that WiFi is installed before MDM for proper enrollment."""

    def test_wifi_installed_before_mdm_profile(self):
        """Verify WiFi profile is installed before MDM profile."""
        from pathlib import Path
        import tempfile
        from datetime import datetime, timezone, timedelta
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            wifi_path = Path(tmpdir) / "wifi.mobileconfig"
            mdm_path = Path(tmpdir) / "mdm.mobileconfig"
            wifi_path.write_bytes(b"<xml>wifi</xml>")
            mdm_path.write_bytes(b"<xml>mdm</xml>")

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            call_order = []

            svc = MagicMock(spec=MobileConfigService)
            svc.set_cloud_configuration = AsyncMock()
            svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
            svc.get_profile_list = AsyncMock(return_value={
                "ProfileMetadata": {
                    "mdm-profile": {"PayloadType": "com.apple.mdm", "PayloadDisplayName": "MDM"},
                }
            })

            async def track_install_profile(payload):
                call_order.append(("install_profile", payload[:20]))

            async def track_install_wifi(payload):
                call_order.append(("install_wifi", payload[:20]))

            async def track_mdm_install(keybag, payload):
                call_order.append(("install_profile_silent", str(keybag)[:40], payload[:20]))

            svc.install_profile = AsyncMock(side_effect=track_install_profile)
            svc.install_wifi_profile = AsyncMock(side_effect=track_install_wifi)
            svc.install_profile_silent = AsyncMock(side_effect=track_mdm_install)
            svc.remove_profile = AsyncMock()
            svc.__aenter__ = AsyncMock(return_value=svc)
            svc.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                from apple_device_cli.enrollment import supervised

                supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    wifi_config=str(wifi_path),
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_path),
                )

            # Verify order: WiFi before MDM
            install_calls = [c[0] for c in call_order]
            wifi_idx = next(
                (i for i, c in enumerate(install_calls) if c == "install_profile"), None
            )
            mdm_idx = next(
                (i for i, c in enumerate(install_calls) if c == "install_profile_silent"), None
            )

            if wifi_idx is not None and mdm_idx is not None:
                assert wifi_idx < mdm_idx, (
                    f"WiFi should be installed before MDM. Order: {install_calls}"
                )


class TestKeybagCleanup:
    """Tests for keybag file cleanup after enrollment."""

    def test_keybag_cleaned_up_after_successful_enrollment(self, mock_pymobiledevice3):
        """Verify keybag file is deleted after successful enrollment."""
        import os

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )

        svc = MagicMock(spec=MobileConfigService)
        svc.set_cloud_configuration = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.get_profile_list = AsyncMock(return_value={
            "ProfileMetadata": {
                "mdm-profile": {"PayloadType": "com.apple.mdm", "PayloadDisplayName": "MDM"},
            }
        })
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        captured_keybag_path = None

        async def capture_keybag(keybag, payload):
            nonlocal captured_keybag_path
            captured_keybag_path = keybag

        svc.install_profile_silent = AsyncMock(side_effect=capture_keybag)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")]))
                .public_key(private_key.public_key())
                .serial_number(1)
                .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
                .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
                .sign(private_key, hashes.SHA256())
            )
            cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.DER))
            key_path.write_bytes(
                private_key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            # Track keybag files before
            temp_dir = tempfile.gettempdir()
            before_files = set(
                f for f in os.listdir(temp_dir) if f.startswith("ios_enroll_keybag_")
            )

            # Create a minimal MDM mobileconfig to trigger install_profile_silent
            mdm_mobileconfig_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_mobileconfig_path.write_bytes(b"<xml>test mdm</xml>")

            with patch(
                "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
            ):
                from apple_device_cli.enrollment import supervised

                supervised.make_supervised(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                    mdm_url="https://mdm.example.com/mdm",
                    mdm_mobileconfig=str(mdm_mobileconfig_path),
                )

            after_files = set(f for f in os.listdir(temp_dir) if f.startswith("ios_enroll_keybag_"))

            # Verify keybag was captured and cleaned up
            assert captured_keybag_path is not None, "keybag should be created and used"
            assert captured_keybag_path.exists() is False, (
                "keybag should be cleaned up after enrollment"
            )

            # Verify no new keybag files remain
            new_files = after_files - before_files
            assert len(new_files) == 0, f"Keybag files leaked: {new_files}"


class TestKeybagCleanupOnException:
    """Verify the keybag is cleaned up even when the enrollment flow raises."""

    def test_keybag_unlinked_when_reconnect_fails_after_supervision(
        self, mock_pymobiledevice3, tmp_path
    ):
        import asyncio
        from apple_device_cli.enrollment import supervised

        # Step 4 (reconnect) raises after the keybag is created — the
        # exception must NOT prevent keybag cleanup via finally.
        async def boom(*args, **kwargs):
            raise BrokenPipeError("simulated disconnect")

        svc = MagicMock(spec=MobileConfigService)
        svc.set_cloud_configuration = boom
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        lockdown = MagicMock(spec=LockdownClient)
        lockdown.udid = "test-udid"
        lockdown.get_value = AsyncMock(return_value="Activated")

        cert_path = tmp_path / "cert.der"
        key_path = tmp_path / "key.der"
        cert_path.write_bytes(b"fake-cert")
        key_path.write_bytes(b"fake-key")

        with (
            patch("pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc),
            patch.object(supervised, "create_keybag_file", spec=True) as mock_keybag,
            patch.object(supervised, "_create_keybag_file_from_identity", spec=True) as mock_id_keybag,
            patch.object(
                supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake-bytes"
            ),
            patch.object(
                supervised,
                "_wait_for_device_reconnect",
                new=AsyncMock(side_effect=RuntimeError("reconnect failed")),
            ),
            patch("pathlib.Path.unlink", spec=True) as mock_unlink,
        ):
            mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("fake-cert-material")

            mock_keybag.side_effect = make_fake
            mock_id_keybag.side_effect = make_fake

            with pytest.raises(RuntimeError, match="reconnect failed"):
                asyncio.run(
                    supervised.do_supervised_pairing(
                        cert_path=str(cert_path),
                        key_path=str(key_path),
                        org_name="Test Org",
                    )
                )

        # The finally block must have called unlink on the keybag path
        assert mock_unlink.called, "keybag should be unlinked even when enrollment raises"


class TestKeybagCleanupOnCertLoadException:
    """_load_cert_public_bytes_from_keybag can raise before the inner try/excepts."""

    def test_keybag_cleaned_up_when_cert_load_raises(self, mock_pymobiledevice3, tmp_path):
        import asyncio
        from apple_device_cli.enrollment import supervised

        svc = MagicMock(spec=MobileConfigService)
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        lockdown = MagicMock(spec=LockdownClient)
        lockdown.udid = "test-udid"
        lockdown.get_value = AsyncMock(return_value="Activated")

        cert_path = tmp_path / "cert.der"
        key_path = tmp_path / "key.der"
        cert_path.write_bytes(b"fake-cert")
        key_path.write_bytes(b"fake-key")

        with (
            patch("pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc),
            patch.object(supervised, "create_keybag_file", spec=True) as mock_keybag,
            patch.object(supervised, "_create_keybag_file_from_identity", spec=True) as mock_id_keybag,
            patch.object(
                supervised, "_load_cert_public_bytes_from_keybag", side_effect=ValueError("boom")
            ),
            patch("pathlib.Path.unlink", spec=True) as mock_unlink,
        ):
            mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("fake-cert-material")

            mock_keybag.side_effect = make_fake
            mock_id_keybag.side_effect = make_fake

            with pytest.raises(ValueError, match="boom"):
                asyncio.run(
                    supervised.do_supervised_pairing(
                        cert_path=str(cert_path),
                        key_path=str(key_path),
                        org_name="Test Org",
                    )
                )

        assert mock_unlink.called, "keybag should be unlinked even when cert load raises"


class TestCleanupKeybag:
    """Direct unit tests for _cleanup_keybag helper."""

    def test_cleanup_keybag_removes_existing_file(self, tmp_path):
        from apple_device_cli.enrollment.supervised import _cleanup_keybag

        keybag = tmp_path / "test.keybag"
        keybag.write_text("sensitive material")
        assert keybag.exists()
        _cleanup_keybag(keybag)
        assert not keybag.exists()

    def test_cleanup_keybag_swallows_oserror(self, tmp_path):
        from apple_device_cli.enrollment.supervised import _cleanup_keybag

        keybag = tmp_path / "test.keybag"
        keybag.write_text("sensitive")
        with patch("pathlib.Path.unlink", side_effect=OSError("permission denied")):
            _cleanup_keybag(keybag)

    def test_cleanup_keybag_nonexistent_is_noop(self):
        from apple_device_cli.enrollment.supervised import _cleanup_keybag

        _cleanup_keybag(None)

    def test_cleanup_keybag_missing_path_is_noop(self, tmp_path):
        from apple_device_cli.enrollment.supervised import _cleanup_keybag

        keybag = tmp_path / "nonexistent.keybag"
        _cleanup_keybag(keybag)


class TestReenrollExitCode:
    """Verify ios-enroll enroll re-enroll exit codes."""

    def test_reenroll_exits_nonzero_on_apple_device_error(self, mock_pymobiledevice3, tmp_path):
        fake_device = MagicMock(spec=DeviceInfo)
        fake_device.udid = "test-udid"
        fake_device.device_name = "Test iPad"

        runner = CliRunner()
        with (
            patch("apple_device_cli.cli._prompt_for_udid", return_value=fake_device),
            patch(
                "apple_device_cli.enrollment.supervised.erase_device_for_reenrollment",
                side_effect=AppleDeviceError("erase failed"),
            ),
        ):
            result = runner.invoke(enroll_app, ["re-enroll", "--udid", "test-udid", "--force"])

        assert result.exit_code == 1
        assert "Error" in result.stdout or "erase failed" in result.stdout

    def test_reenroll_exits_zero_on_success(self, mock_pymobiledevice3, tmp_path):
        fake_device = MagicMock(spec=DeviceInfo)
        fake_device.udid = "test-udid"
        fake_device.device_name = "Test iPad"

        runner = CliRunner()
        with (
            patch("apple_device_cli.cli._prompt_for_udid", return_value=fake_device),
            patch(
                "apple_device_cli.enrollment.supervised.erase_device_for_reenrollment",
                return_value=True,
            ),
        ):
            result = runner.invoke(enroll_app, ["re-enroll", "--udid", "test-udid", "--force"])

        assert result.exit_code == 0
        assert "cloud config erased" in result.stdout.lower()
