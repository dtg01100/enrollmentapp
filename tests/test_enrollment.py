"""Tests for apple_device_cli.enrollment.supervised make_supervised() flow.

The shared pymobiledevice3 mock fixture lives in tests/conftest.py; this module
imports the mock exception classes from there and re-exports them under the
short names the test bodies use.
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from unittest.mock import MagicMock, patch, AsyncMock

from tests.conftest import (
    MockNoDeviceConnectedError as _NoDeviceConnectedError,
    LockdownClient,
    MobileActivationService,
    MobileConfigService,
)


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

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )
        svc = MagicMock(spec=MobileConfigService)
        svc.store_profile = AsyncMock()
        svc.get_cloud_configuration = AsyncMock(
            return_value={"MDMServerURL": "https://mdm.example.com/mdm", "IsSupervised": True}
        )
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

        lockdown = MagicMock(spec=LockdownClient)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Activated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )
        svc = MagicMock(spec=MobileConfigService)
        svc.install_wifi_profile = AsyncMock()
        svc.install_profile_silent = AsyncMock()
        svc.store_profile = AsyncMock()
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
            # Patch B: SSID/password wifi path now uses install_profile_silent with a built plist
            svc.install_profile_silent.assert_awaited_once()
            svc.install_wifi_profile.assert_not_called()
            assert result.wifi_installed is True

    def test_make_supervised_installs_wifi_mobileconfig(self, mock_pymobiledevice3):
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
        svc.install_profile = AsyncMock()
        svc.install_profile_silent = AsyncMock()
        svc.get_profile_list = AsyncMock(return_value={})
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            wifi_config_path = Path(tmpdir) / "wifi.mobileconfig"
            wifi_config_path.write_bytes(b"fake-mobileconfig-content")

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
            # Patch B: wifi.mobileconfig path now uses install_profile_silent with keybag
            svc.install_profile_silent.assert_awaited_once()
            svc.install_profile.assert_not_called()
            assert result.wifi_installed is True

    def test_make_supervised_normalizes_quoted_wifi_mobileconfig_path(self, mock_pymobiledevice3):
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
        svc.install_profile = AsyncMock()
        svc.install_profile_silent = AsyncMock()
        svc.get_profile_list = AsyncMock(return_value={})
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            wifi_config_path = Path(tmpdir) / "wifi.mobileconfig"
            wifi_config_path.write_bytes(b"fake-mobileconfig-content")

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
        # Patch B: wifi.mobileconfig path now uses install_profile_silent with keybag
        svc.install_profile_silent.assert_awaited_once()
        svc.install_profile.assert_not_called()
        assert result.wifi_installed is True

    def test_mobileconfig_error_formatter_extracts_concise_network_error(
        self, mock_pymobiledevice3
    ):
        from apple_device_cli.enrollment import supervised

        error = Exception(
            "invalid response {'ErrorChain': [{'ErrorCode': 4001, 'LocalizedDescription': 'Profile Installation Failed'}, {'ErrorCode': -1009, 'LocalizedDescription': 'The Internet connection appears to be offline.'}], 'Status': 'Error'}"
        )

        formatted = supervised._format_mobileconfig_error("MDM profile install failed", error)

        assert (
            formatted
            == "MDM profile install failed: The Internet connection appears to be offline."
        )

    def test_make_supervised_recognizes_nested_mdm_envelope(self, mock_pymobiledevice3):
        """End-to-end regression: SimpleMDM-style nested MDM profile.

        The MDM profile is installed via install_profile_silent and the
        device's get_profile_list returns a Configuration envelope whose
        identifier contains 'mdm' (no PayloadType exposed). The pre-fix
        verification returned False for this case and falsely reported
        "MDM profile not found on device after install" — preventing
        otherwise-successful enrollments from being recorded as success.
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

        mdm_topic = "com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f"
        # iOS 26 metadata: no PayloadType, just display fields.
        nested_envelope_metadata = {
            "ProfileMetadata": {
                "com.unwiredmdm.mobileconfig.profile-service": {
                    "PayloadDisplayName": "Capital Candy Company Profile",
                    "PayloadOrganization": "Capital Candy Company",
                    "PayloadUUID": "ddb4c3b5-8357-4b2c-8b23-4e75dfdf78a1",
                    "PayloadVersion": 1,
                    "PayloadRemovalDisallowed": False,
                }
            }
        }

        svc = MagicMock(spec=MobileConfigService)
        svc.install_profile_silent = AsyncMock()
        svc.get_profile_list = AsyncMock(return_value=nested_envelope_metadata)
        svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            mdm_config_path = Path(tmpdir) / "mdm.mobileconfig"
            mdm_config_path.write_bytes(b"fake-mdm-mobileconfig-content")

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
                    str(cert_path),
                    str(key_path),
                    "Test Org",
                    None,
                    ["passcode"],
                    "https://mdm.example.com/mdm",
                    None,
                    None,
                    "WPA",
                    "https://mdm.example.com/checkin",
                    mdm_topic,
                    False,
                    None,
                    str(mdm_config_path),
                )

        assert result.success is True, f"Expected success, got errors: {result.errors}"
        assert result.mdm_enrolled is True
        assert result.errors == []


class TestActivation:
    def test_module_imports(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import activation

        assert hasattr(activation, "do_activate")
        assert hasattr(activation, "activate_device")

    def test_activate_device_without_hardware(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import activation
        from apple_device_cli.core.exceptions import ActivationError

        with patch.object(
            activation,
            "create_using_usbmux",
            AsyncMock(side_effect=_NoDeviceConnectedError("No device")),
        ):
            with pytest.raises(ActivationError):
                activation.activate_device()


class TestProfileListContainsMdm:
    """Tests for the helper that detects MDM enrollment in get_profile_list output.

    The original check looked only at top-level ``PayloadType == "com.apple.mdm"``
    and returned False for nested payloads (SimpleMDM-style Configuration envelopes).
    These tests pin down the new behavior so the regression cannot return.
    """

    def test_returns_false_for_none(self):
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        assert _profile_list_contains_mdm(None) is False

    def test_returns_false_for_non_dict(self):
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        assert _profile_list_contains_mdm([]) is False
        assert _profile_list_contains_mdm("string") is False

    def test_returns_false_for_empty_profiles(self):
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        assert _profile_list_contains_mdm({}) is False
        assert _profile_list_contains_mdm({"ProfileMetadata": {}}) is False

    def test_returns_false_when_only_wifi_profile(self):
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "com.apple.wifi.managed": {
                    "PayloadDisplayName": "Test WiFi",
                    "PayloadUUID": "00000000-0000-0000-0000-000000000001",
                    "PayloadVersion": 1,
                }
            }
        }
        assert _profile_list_contains_mdm(profiles) is False

    def test_returns_true_for_top_level_mdm_payload(self):
        """A profile with PayloadType com.apple.mdm at the top level."""
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "com.apple.mdm": {
                    "PayloadType": "com.apple.mdm",
                    "PayloadDisplayName": "MDM",
                    "PayloadUUID": "00000000-0000-0000-0000-000000000002",
                    "PayloadVersion": 1,
                }
            }
        }
        assert _profile_list_contains_mdm(profiles) is True

    def test_returns_true_for_nested_mdm_payload_in_simplemdm_envelope(self):
        """SimpleMDM and similar vendors wrap com.apple.mdm in a Configuration envelope.

        This is the case that broke in production: the outer profile's
        PayloadType is 'Configuration', and the inner com.apple.mdm payload
        is only visible if the iOS metadata includes PayloadContent. When
        PayloadContent is present, the helper walks it.
        """
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "com.unwiredmdm.mobileconfig.profile-service": {
                    "PayloadType": "Configuration",
                    "PayloadDisplayName": "Capital Candy Company Profile",
                    "PayloadIdentifier": "com.unwiredmdm.mobileconfig.profile-service",
                    "PayloadUUID": "ddb4c3b5-8357-4b2c-8b23-4e75dfdf78a1",
                    "PayloadVersion": 1,
                    "PayloadContent": [
                        {
                            "PayloadType": "com.apple.security.scep",
                            "PayloadIdentifier": "f459cdf13a0b40ff8ab05c3961deff6a",
                            "URL": "https://a.simplemdm.com/scep",
                        },
                        {
                            "PayloadType": "com.apple.mdm",
                            "PayloadIdentifier": "com.apple.mdm",
                            "ServerURL": "https://a.simplemdm.com/mdm",
                            "Topic": "com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f",
                        },
                    ],
                }
            }
        }
        assert _profile_list_contains_mdm(profiles) is True

    def test_returns_true_for_nested_mdm_payload_via_topic_match(self):
        """When PayloadContent is not surfaced (typical on iOS 26 metadata),
        the helper matches the nested MDM payload's Topic against the
        expected_topic argument. This is the most reliable production path.
        """
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "com.unwiredmdm.mobileconfig.profile-service": {
                    "PayloadType": "Configuration",
                    "PayloadDisplayName": "Capital Candy Company Profile",
                    "PayloadIdentifier": "com.unwiredmdm.mobileconfig.profile-service",
                    "PayloadUUID": "ddb4c3b5-8357-4b2c-8b23-4e75dfdf78a1",
                    "PayloadVersion": 1,
                }
            }
        }
        assert (
            _profile_list_contains_mdm(
                profiles,
                expected_topic="com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f",
            )
            is True
        )

    def test_returns_true_when_identifier_contains_mdm_keyword(self):
        """Fallback heuristic: SimpleMDM uses identifiers like
        'com.unwiredmdm.mobileconfig.profile-service' which contain 'mdm'.
        This catches the case where neither PayloadType nor PayloadContent
        is exposed in the metadata (the common iOS 26 case).
        """
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "com.unwiredmdm.mobileconfig.profile-service": {
                    "PayloadDisplayName": "Capital Candy Company Profile",
                    "PayloadUUID": "ddb4c3b5-8357-4b2c-8b23-4e75dfdf78a1",
                    "PayloadVersion": 1,
                }
            }
        }
        assert _profile_list_contains_mdm(profiles) is True

    def test_returns_false_when_identifier_contains_mdm_but_not_in_metadata(self):
        """The 'mdm' in the identifier check must not trigger on random
        strings — only when the identifier is actually present and looks
        like an MDM-vendor identifier."""
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "Davids-MacBook-Pro.43E2EA9D-7A96-41E9-B400-17CBE7A12DB4": {
                    "PayloadDisplayName": "Wi-Fi",
                    "PayloadUUID": "40BE9F2D-91BB-4AC8-B61B-4F55260C2529",
                    "PayloadVersion": 1,
                }
            }
        }
        assert _profile_list_contains_mdm(profiles) is False

    def test_returns_false_when_metadata_values_are_not_dicts(self):
        """Defensive: a malformed ProfileMetadata entry should not crash."""
        from apple_device_cli.enrollment.supervised import _profile_list_contains_mdm

        profiles = {
            "ProfileMetadata": {
                "bad.entry": "not a dict",
                "Davids-MacBook-Pro.43E2EA9D": None,
            }
        }
        assert _profile_list_contains_mdm(profiles) is False
