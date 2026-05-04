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


class CloudConfigurationAlreadyPresentError(Exception):
    """Mock exception for testing."""


class TestSupervisedPairing:
    def test_module_imports(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised
        assert hasattr(supervised, "do_supervised_pairing")
        assert hasattr(supervised, "make_supervised")

    def test_make_supervised_with_invalid_paths(self, mock_pymobiledevice3):
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=_NoDeviceConnectedError("No device")
        )
        from apple_device_cli.enrollment import supervised
        # Invalid cert/key paths now return EnrollmentResult with success=False
        # instead of raising EnrollmentError
        result = supervised.make_supervised(
            "/nonexistent/cert.der",
            "/nonexistent/key.der",
            "Test Org",
            None,
            ["Location", "ApplePay"],
        )
        assert result.success is False
        assert len(result.errors) > 0
        assert "Certificate not found" in result.errors[0]

    def test_make_supervised_installs_mdm_profile(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock()
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock()
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = activation_svc

        svc = AsyncMock()
        svc.store_profile = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(return_value={"MDMServerURL": "https://mdm.example.com/mdm", "IsSupervised": True})
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
                    ["restore-completed"],
                    "https://mdm.example.com/mdm",
                    "MySSID",
                    "password123",
                    "WPA",
                    "https://mdm.example.com/checkin",
                    "com.example.topic",
                )

        svc.set_cloud_configuration.assert_awaited()
        cloud_config = svc.set_cloud_configuration.call_args.args[0]
        assert cloud_config["OrganizationName"] == "Test Org"
        assert "RestoreCompleted" in cloud_config["SkipSetup"]
        assert cloud_config["SupervisorHostCertificates"]
        assert cloud_config["MDMServerURL"] == "https://mdm.example.com/mdm"
        assert result.success is True
        assert result.mdm_enrolled is True
        # MDM enrollment uses cloud config (MDMServerURL) - the device enrolls via SCEP
        # on first boot through Setup Assistant, so store_profile is not called
        svc.store_profile.assert_not_called()

    def test_make_supervised_installs_wifi_profile(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock()
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock()
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = activation_svc

        svc = AsyncMock()
        svc.install_wifi_profile = AsyncMock()
        svc.store_profile = AsyncMock()
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
                    ["passcode"],
                    None,
                    "OfficeWiFi",
                    "wifipass123",
                    "WPA",
                )

            svc.install_wifi_profile.assert_awaited_once()
            call_kwargs = svc.install_wifi_profile.call_args.kwargs
            assert call_kwargs["ssid"] == "OfficeWiFi"
            assert call_kwargs["password"] == "wifipass123"
            assert call_kwargs["encryption_type"] == "WPA"
            assert result.wifi_installed is True

    def test_make_supervised_installs_wifi_mobileconfig(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock()
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock()
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = activation_svc

        svc = AsyncMock()
        svc.install_profile = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__.return_value = svc
        svc.__aexit__.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            wifi_config_path = Path(tmpdir) / "wifi.mobileconfig"
            wifi_config_path.write_bytes(b"fake-mobileconfig-content")

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
                    ["passcode"],
                    None,
                    None,
                    None,
                    "WPA",
                    None,
                    None,
                    False,
                    str(wifi_config_path),
                )

            svc.install_profile.assert_awaited_once()
            assert result.wifi_installed is True

    def test_make_supervised_normalizes_quoted_wifi_mobileconfig_path(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock()
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock()
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = activation_svc

        svc = AsyncMock()
        svc.install_profile = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__.return_value = svc
        svc.__aexit__.return_value = False

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            wifi_config_path = Path(tmpdir) / "wifi.mobileconfig"
            wifi_config_path.write_bytes(b"fake-mobileconfig-content")

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
                    ["passcode"],
                    None,
                    None,
                    None,
                    "WPA",
                    None,
                    None,
                    False,
                    f" '{wifi_config_path}' ",
                )

        svc.install_profile.assert_awaited_once()
        assert result.wifi_installed is True

    def test_mobileconfig_error_formatter_extracts_concise_network_error(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        error = Exception(
            "invalid response {'ErrorChain': [{'ErrorCode': 4001, 'LocalizedDescription': 'Profile Installation Failed'}, {'ErrorCode': -1009, 'LocalizedDescription': 'The Internet connection appears to be offline.'}], 'Status': 'Error'}"
        )

        formatted = supervised._format_mobileconfig_error("MDM profile install failed", error)

        assert formatted == "MDM profile install failed: The Internet connection appears to be offline."


class TestActivation:
    def test_module_imports(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import activation
        assert hasattr(activation, "do_activate")
        assert hasattr(activation, "activate_device")

    def test_activate_device_without_hardware(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import activation
        from apple_device_cli.core.exceptions import ActivationError
        with patch.object(activation, 'create_using_usbmux', AsyncMock(
                side_effect=_NoDeviceConnectedError("No device")
            )):
            with pytest.raises(ActivationError):
                activation.activate_device()