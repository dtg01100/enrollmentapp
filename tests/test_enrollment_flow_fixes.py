"""Tests for enrollment flow fixes: cloud config, state management, error handling."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from unittest.mock import MagicMock, patch, AsyncMock


class _NoDeviceConnectedError(Exception):
    """Simulates pymobiledevice3.lockdown.NoDeviceConnectedError for testing."""


class CloudConfigurationAlreadyPresentError(Exception):
    """Mock exception for testing."""


@pytest.fixture(autouse=True)
def mock_pymobiledevice3():
    mock_pm3 = MagicMock()
    mock_pm3.lockdown.create_using_usbmux = AsyncMock()
    mock_pm3.lockdown.LockdownClient = MagicMock()
    mock_pm3.lockdown.NoDeviceConnectedError = _NoDeviceConnectedError

    mock_mobile_config = MagicMock()
    mock_mobile_config.MobileConfigService = MagicMock()
    mock_mobile_config.CloudConfigurationAlreadyPresentError = CloudConfigurationAlreadyPresentError
    mock_pm3.services.mobile_config = mock_mobile_config

    mock_mobile_activation = MagicMock()
    mock_mobile_activation.MobileActivationService = MagicMock()
    mock_pm3.services.mobile_activation = mock_mobile_activation

    mock_pm3.ca.create_keybag_file = MagicMock()

    with patch.dict('sys.modules', {
        'pymobiledevice3': mock_pm3,
        'pymobiledevice3.lockdown': mock_pm3.lockdown,
        'pymobiledevice3.services': MagicMock(),
        'pymobiledevice3.services.mobile_config': mock_pm3.services.mobile_config,
        'pymobiledevice3.services.mobile_activation': mock_pm3.services.mobile_activation,
        'pymobiledevice3.ca': mock_pm3.ca,
    }):
        yield mock_pm3


class TestCloudConfigBugFix:
    """Test that cloud config is always set, not just when skip_list provided."""

    def test_make_supervised_sets_cloud_config_without_skip_list(self, mock_pymobiledevice3):
        """Test: Cloud config is set even when skip_list is None."""
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock()
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock()
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = activation_svc

        svc = AsyncMock()
        svc.supervise = AsyncMock()
        svc.set_cloud_configuration = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__.return_value = svc
        svc.__aexit__.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"

            private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, "Test Org"),
            ])
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

            with patch('pymobiledevice3.services.mobile_config.MobileConfigService', return_value=svc):
                result = supervised.make_supervised(
                    str(cert_path),
                    str(key_path),
                    "Test Org",
                    None,
                    skip_list=None,  # No skip_list provided
                )

        # Verify set_cloud_configuration was called
        assert svc.set_cloud_configuration.called
        call_args = svc.set_cloud_configuration.call_args.args[0]
        assert call_args["IsSupervised"] is True
        assert call_args["OrganizationName"] == "Test Org"
        # SkipSetup should NOT be in config if skip_list is None
        assert "SkipSetup" not in call_args


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
