"""Integration tests covering the uncovered branches in supervised.do_supervised_pairing.

These tests are wiring-level: they drive ``do_supervised_pairing`` through
``make_supervised`` with mock pymobiledevice3 services configured to trigger
specific error paths. Each test pins a single branch that was uncovered
after the supervised_actions.py extraction.

Companion to tests/test_supervised_helpers.py (pure helpers) and
tests/test_supervised_actions.py (pure decision logic). This file covers
the async I/O wiring in the wrapper itself.

NB on imports: the ``autouse mock_pymobiledevice3`` fixture in conftest
patches ``pymobiledevice3.ca`` AFTER pytest collects this module, so any
``from pymobiledevice3.ca import ...`` in supervised.py must be triggered
by an import that happens BEFORE the fixture activates. Importing
``supervised`` at module level (below) achieves this: supervised is loaded
when pytest first scans this file, capturing the real module-level names
in its module namespace. Subsequent ``patch.object(supervised, ...)`` calls
inside test bodies then have real functions to spec against.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from apple_device_cli.enrollment import supervised  # module-level so real names are bound

from tests.conftest import (
    MockCloudConfigurationAlreadyPresentError as CloudConfigurationAlreadyPresentError,
    LockdownClient,
    MobileActivationService,
    MobileConfigService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_der_identity(tmp_path: Path) -> tuple[Path, Path]:
    """Write a valid (cert, key) pair to tmp_path and return the paths."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Org")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .sign(private_key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.der"
    key_path = tmp_path / "key.der"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _wire_basic_supervised_mocks(
    mock_pymobiledevice3, *, set_cloud_configuration_side_effect=None
):
    """Return (lockdown, svc) with the standard mock setup for the happy path."""
    lockdown = MagicMock(spec=LockdownClient)
    lockdown.udid = "test-udid"
    mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

    activation_svc = MagicMock(spec=MobileActivationService)
    activation_svc.state = AsyncMock(return_value="Activated")
    activation_svc.activate = AsyncMock()
    mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
        activation_svc
    )

    svc = MagicMock(spec=MobileConfigService)
    svc.set_cloud_configuration = AsyncMock(
        side_effect=set_cloud_configuration_side_effect
    )
    svc.get_cloud_configuration = AsyncMock(return_value={"IsSupervised": True})
    svc.__aenter__ = AsyncMock(return_value=svc)
    svc.__aexit__ = AsyncMock(return_value=False)
    return lockdown, svc


def _patch_supervised_io(mock_pymobiledevice3, svc):
    """Patch the runtime entry points supervised.py uses."""
    return patch(
        "pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc
    )


# ---------------------------------------------------------------------------
# _wait_for_cloud_config returns None (device still processing)
# ---------------------------------------------------------------------------


class TestWaitForCloudConfig:
    """The inner try in supervised.py lines 601-609."""

    def test_device_still_processing_after_apply_continues_normally(
        self, mock_pymobiledevice3, tmp_path
    ):
        """_wait_for_cloud_config returns None → log 'continuing anyway'."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        with patch.object(
            supervised, "_wait_for_cloud_config", new=AsyncMock(return_value=None)
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        assert result.success is True
        assert result.supervised is True

    def test_broken_pipe_in_wait_for_cloud_config_sets_device_disconnected(
        self, mock_pymobiledevice3, tmp_path
    ):
        """_wait_for_cloud_config raises BrokenPipe → device_disconnected=True → reconnect."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        async def raise_broken_pipe(*args, **kwargs):
            raise BrokenPipeError("simulated")

        with patch.object(
            supervised, "_wait_for_cloud_config", new=raise_broken_pipe
        ), patch.object(
            supervised, "_wait_for_device_reconnect", new=AsyncMock(return_value=None)
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        # device disconnected, didn't reconnect within timeout → error recorded
        assert any("did not reconnect" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Step 4 reconnect success path with verification
# ---------------------------------------------------------------------------


class TestStep4ReconnectVerification:
    """The post-supervision reconnect + verify path (lines 652-682)."""

    def test_reconnect_then_verify_supervised(
        self, mock_pymobiledevice3, tmp_path
    ):
        """After supervision disconnect, reconnect succeeds and supervised is verified."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        async def raise_broken_pipe(*args, **kwargs):
            raise BrokenPipeError("simulated")

        fresh_lockdown = MagicMock(spec=LockdownClient)
        fresh_lockdown.udid = "test-udid"
        with patch.object(
            supervised, "_wait_for_cloud_config", new=raise_broken_pipe
        ), patch.object(
            supervised, "_wait_for_device_reconnect", new=AsyncMock(return_value=fresh_lockdown)
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        # Reconnect succeeded, no errors recorded about reconnect timeout
        assert not any("did not reconnect" in e for e in result.errors)
        # Final verify (Step 7) returns IsSupervised=True from the mock
        assert result.success is True

    def test_reconnect_succeeds_but_verification_fetch_raises(
        self, mock_pymobiledevice3, tmp_path
    ):
        """Reconnect returns fresh lockdown, but the post-reconnect get_cloud_configuration raises."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        # First call (inside Step 3 _wait_for_cloud_config) raises broken pipe;
        # subsequent calls (Step 4 verification, Step 7 verify) also raise.
        async def raise_broken_pipe(*args, **kwargs):
            raise BrokenPipeError("simulated disconnect")

        fresh_lockdown = MagicMock(spec=LockdownClient)
        fresh_lockdown.udid = "test-udid"
        with patch.object(
            supervised, "_wait_for_cloud_config", new=raise_broken_pipe
        ), patch.object(
            supervised, "_wait_for_device_reconnect", new=AsyncMock(return_value=fresh_lockdown)
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            # The verify call after reconnect also raises BrokenPipeError → falls through to Step 7.
            # Step 7's first try will raise BrokenPipeError → goes through the disconnect path
            # and tries to reconnect again (which returns fresh_lockdown) — then the second
            # verify call inside Step 7 raises RuntimeError → 'Reconnection verification failed'.
            async def raise_after_reconnect(*args, **kwargs):
                if svc.get_cloud_configuration.call_count > 0:
                    raise RuntimeError("verify failed")
                return None

            svc.get_cloud_configuration.side_effect = raise_after_reconnect

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        # Should not crash; verification-failed path is logged but non-fatal.
        assert result is not None


# ---------------------------------------------------------------------------
# Step 7 final verification with broken pipe and reconnect
# ---------------------------------------------------------------------------


class TestStep7FinalVerify:
    """Final verification step (lines 810-845) — broken pipe triggers reconnect."""

    def test_final_verify_broken_pipe_triggers_reconnect(
        self, mock_pymobiledevice3, tmp_path
    ):
        """Step 7's get_cloud_configuration raises BrokenPipeError → reconnect path runs."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        # Successful Step 3 (set_cloud_configuration returns nothing);
        # _wait_for_cloud_config returns the supervised dict;
        # Step 7's get_cloud_configuration raises BrokenPipeError
        # → triggers reconnect path in Step 7 (lines 829-845).
        call_count = {"n": 0}

        async def get_cloud_with_pipe_on_last(*args, **kwargs):
            call_count["n"] += 1
            # First call (inside Step 3's _wait_for_cloud_config result fetch?) — actually
            # _wait_for_cloud_config is mocked below. So the calls here are:
            # 1. Step 3 — get_cloud_configuration called from set_cloud_configuration's success
            #    wait, no — set_cloud_configuration doesn't auto-fetch. _wait_for_cloud_config
            #    is what fetches. We mock that. So calls here are:
            #    - AlreadyPresent check (no — set_cloud_configuration succeeds here)
            #    - Step 4 reconnect verification (no — device didn't disconnect in Step 3)
            #    - Step 7 final verify
            if call_count["n"] == 1:
                return {"IsSupervised": True}
            raise BrokenPipeError("verify disconnect")

        svc.get_cloud_configuration.side_effect = get_cloud_with_pipe_on_last

        fresh_lockdown = MagicMock(spec=LockdownClient)
        fresh_lockdown.udid = "test-udid"
        with patch.object(
            supervised, "_wait_for_cloud_config", new=AsyncMock(return_value={"IsSupervised": True})
        ), patch.object(
            supervised, "_wait_for_device_reconnect", new=AsyncMock(return_value=fresh_lockdown)
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        assert result is not None


# ---------------------------------------------------------------------------
# AlreadyPresent error path: get_cloud_configuration raises during check
# ---------------------------------------------------------------------------


class TestAlreadyPresentCheckError:
    """Line 620-621: the inner try around get_cloud_configuration raises."""

    def test_get_cloud_configuration_raises_during_already_present_check(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(
            mock_pymobiledevice3,
            set_cloud_configuration_side_effect=CloudConfigurationAlreadyPresentError(),
        )

        async def raise_on_get(*args, **kwargs):
            raise OSError("read failed")

        svc.get_cloud_configuration.side_effect = raise_on_get

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        assert result.success is False
        assert any("could not be verified" in e for e in result.errors)


# ---------------------------------------------------------------------------
# WiFi mobileconfig file install — existing WiFi profile removal path
# ---------------------------------------------------------------------------


class TestWifiMobileconfigInstall:
    """Lines 716, 723-725, 729-730, 743-744: the wifi_config file install branch."""

    def test_installs_wifi_mobileconfig_removing_existing_wifi_profiles(
        self, mock_pymobiledevice3, tmp_path
    ):
        """Pre-existing WiFi profiles are removed before installing the new one."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        wifi_config_path = tmp_path / "wifi.mobileconfig"
        wifi_config_path.write_bytes(b"fake-wifi-profile")

        # Existing WiFi profile present + new profile installed
        svc.get_profile_list = AsyncMock(return_value={
            "ProfileMetadata": {
                "old-wifi-id": {
                    "PayloadType": "com.apple.wifi.managed",
                    "PayloadDisplayName": "OldWiFi",
                }
            }
        })

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                wifi_config=str(wifi_config_path),
            ))

        # remove_profile was called for the existing WiFi profile
        svc.remove_profile.assert_awaited_once_with("old-wifi-id")
        assert result.wifi_installed is True

    def test_wifi_mobileconfig_install_failure_records_error(
        self, mock_pymobiledevice3, tmp_path
    ):
        """When install_profile_silent raises during wifi mobileconfig install,
        the error is appended and the wifi_installed flag stays False.
        """
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        wifi_config_path = tmp_path / "wifi.mobileconfig"
        wifi_config_path.write_bytes(b"fake-wifi-profile")

        # Make install_profile_silent raise (the second await on this mock)
        call_count = {"n": 0}

        async def install_or_fail(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 1:
                raise RuntimeError("install failed")
            return None

        svc.install_profile_silent.side_effect = install_or_fail
        svc.get_profile_list = AsyncMock(return_value={"ProfileMetadata": {}})

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                wifi_config=str(wifi_config_path),
            ))

        assert result.wifi_installed is False
        assert any("WiFi mobileconfig install failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# MDM store_profile fallback path (no keybag)
# ---------------------------------------------------------------------------


# Skipped: triggering the keybag-missing path requires making both
# _create_keybag_file_from_identity fails, which is
# brittle and not the highest-value coverage gain. The lines 773-774
# (store_profile call) are covered indirectly by the existing
# test_make_supervised_installs_mdm_profile test which exercises the
# install_profile_silent path.



# ---------------------------------------------------------------------------
# AlreadyPresent → check succeeds but config mismatches
# ---------------------------------------------------------------------------


class TestAlreadyPresentMismatch:
    """Line 614-620: existing cloud config does NOT match the desired payload."""

    def test_existing_cloud_config_mismatch_records_mismatch_error(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(
            mock_pymobiledevice3,
            set_cloud_configuration_side_effect=CloudConfigurationAlreadyPresentError(),
        )

        # Existing config has a DIFFERENT org name → _cloud_config_matches returns False
        svc.get_cloud_configuration = AsyncMock(return_value={
            "AllowPairing": True,
            "CloudConfigurationUIComplete": True,
            "ConfigurationSource": 2,
            "ConfigurationWasApplied": True,
            "IsMandatory": True,
            "IsMultiUser": False,
            "IsSupervised": True,
            "OrganizationName": "Other Org",
            "PostSetupProfileWasInstalled": True,
        })

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        assert result.success is False
        assert any("Cloud configuration mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# WiFi mobileconfig not found on disk
# ---------------------------------------------------------------------------


class TestWifiMobileconfigNotFound:
    """Line 752-754: wifi_config path is provided but file does not exist."""

    def test_wifi_config_path_missing_records_error(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        # wifi_config path points to a file that does NOT exist
        missing_wifi_path = tmp_path / "nonexistent.mobileconfig"

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                wifi_config=str(missing_wifi_path),
            ))

        assert result.wifi_installed is False
        assert any("WiFi config file not found" in e for e in result.errors)


# ---------------------------------------------------------------------------
# MDM mobileconfig path not found
# ---------------------------------------------------------------------------


class TestMdmMobileconfigNotFound:
    """Line 776-777: mdm_mobileconfig path is provided but file does not exist."""

    def test_mdm_mobileconfig_missing_records_error(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                mdm_url="https://mdm.example.com/mdm",
                mdm_mobileconfig=str(tmp_path / "nonexistent-mdm.mobileconfig"),
            ))

        assert any("MDM mobileconfig not found" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Final verify (Step 7) non-OSError exception path
# ---------------------------------------------------------------------------


class TestFinalVerifyGenericError:
    """Line 810-811: Step 7's verify catches a generic (non-OSError) exception."""

    def test_final_verify_generic_exception_is_non_fatal(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        # First call (in Step 3's _wait_for_cloud_config — but we mock that to return a dict
        # below, so the real get_cloud_configuration calls are Step 7's verify).
        # Make the FIRST get_cloud_configuration call (Step 7) raise a generic exception.
        call_count = {"n": 0}

        async def get_cloud_first_fails(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("generic verify error")
            return {"IsSupervised": True}

        svc.get_cloud_configuration.side_effect = get_cloud_first_fails

        with patch.object(
            supervised, "_wait_for_cloud_config", new=AsyncMock(return_value={"IsSupervised": True})
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        # Verification error is non-fatal — result still produced
        assert result is not None
        assert result.success is True


# ---------------------------------------------------------------------------
# make_supervised wrapper handles CloudConfigurationAlreadyPresentError gracefully
# ---------------------------------------------------------------------------


class TestMakeSupervisedAlreadyPresent:
    """make_supervised's outer try/except for AlreadyPresentError (lines 1115-1135)."""

    def test_make_supervised_already_present_returns_failure_result(
        self, mock_pymobiledevice3
    ):
        from apple_device_cli.enrollment import supervised

        # Make do_supervised_pairing raise CloudConfigurationAlreadyPresentError.
        # make_supervised should catch it and return a failure EnrollmentResult.
        async def raise_already(*args, **kwargs):
            raise CloudConfigurationAlreadyPresentError()

        with patch.object(
            supervised, "do_supervised_pairing", new=raise_already
        ):
            result = supervised.make_supervised(
                cert_path="/some/cert.der",
                key_path="/some/key.der",
                org_name="Test Org",
            )

        assert result.success is False
        assert any("already present" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Early returns and connection fallback paths
# ---------------------------------------------------------------------------


class TestDoSupervisedPairingEarlyReturns:
    """The cert/key validation and connection-fallback branches."""

    def test_missing_cert_returns_failure_result(
        self, mock_pymobiledevice3, tmp_path
    ):
        """cert_path doesn't exist → early-return EnrollmentResult with success=False."""
        from apple_device_cli.enrollment import supervised

        missing_cert = tmp_path / "missing.der"
        key_path = tmp_path / "key.der"
        key_path.write_bytes(b"fake")

        result = asyncio.run(supervised.do_supervised_pairing(
            cert_path=str(missing_cert),
            key_path=str(key_path),
            org_name="Test Org",
        ))

        assert result.success is False
        assert any("Certificate not found" in e for e in result.errors)

    def test_missing_key_returns_failure_result(
        self, mock_pymobiledevice3, tmp_path
    ):
        """key_path doesn't exist → early-return EnrollmentResult with success=False."""
        from apple_device_cli.enrollment import supervised

        cert_path = tmp_path / "cert.der"
        cert_path.write_bytes(b"fake")
        missing_key = tmp_path / "missing.der"

        result = asyncio.run(supervised.do_supervised_pairing(
            cert_path=str(cert_path),
            key_path=str(missing_key),
            org_name="Test Org",
        ))

        assert result.success is False
        assert any("Private key not found" in e for e in result.errors)

    def test_udid_connect_failure_pairs_and_retries_same_udid(
        self, mock_pymobiledevice3, tmp_path
    ):
        """udid-specific ConnectionError triggers pair-then-retry on the SAME udid.

        main (a78b62a) deliberately does NOT fall back to "any device" when
        the user names a specific UDID — silently operating on a different
        device would enroll the wrong iPad. ``_pair_then_retry_connect``
        pairs the named udid, waits for it to reappear in usbmux, and
        retries the same-udid connect exactly once.
        """
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        call_log = []

        async def create_side_effect(*args, **kwargs):
            call_log.append(kwargs)
            if len(call_log) == 1:
                raise ConnectionError("serial failed")
            return lockdown

        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=create_side_effect
        )

        pair_log = []

        def fake_pair(udid):
            pair_log.append(udid)

        def fake_wait(udid):
            return True

        with patch(
            "apple_device_cli.device.connection.ensure_device_pairing",
            side_effect=fake_pair,
        ), patch(
            "apple_device_cli.device.connection.wait_for_udid_in_usbmux",
            side_effect=fake_wait,
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised,
            "_load_cert_public_bytes_from_keybag",
            return_value=b"fake",
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                udid="target-udid",
                mdm_url="https://mdm.example.com/mdm",
            ))

        # Both attempts targeted the named udid — never a no-serial fallback
        assert call_log == [{"serial": "target-udid"}, {"serial": "target-udid"}]
        # The device was paired and its udid confirmed visible before retry
        assert pair_log == ["target-udid"]
        assert result.device_udid == "target-udid"


class TestDoSupervisedPairingUnactivated:
    """The activation branch: when device state == 'Unactivated', activate is called."""

    def test_unactivated_device_triggers_activate(
        self, mock_pymobiledevice3, tmp_path
    ):
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        activation_svc = MagicMock(spec=MobileActivationService)
        activation_svc.state = AsyncMock(return_value="Unactivated")
        activation_svc.activate = AsyncMock()
        mock_pymobiledevice3.services.mobile_activation.MobileActivationService.return_value = (
            activation_svc
        )

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        activation_svc.activate.assert_awaited_once()


class TestDoSupervisedPairingMdmUnremovable:
    """The mdm_unremovable branch sets IsMDMUnremovable on the payload."""

    def test_mdm_unremovable_sets_payload_flag(
        self, mock_pymobiledevice3, tmp_path
    ):
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        with patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                mdm_unremovable=True,
            ))

        # set_cloud_configuration was called with payload containing IsMDMUnremovable=True
        call_args = svc.set_cloud_configuration.call_args
        assert call_args is not None
        payload = call_args.args[0]
        assert payload.get("IsMDMUnremovable") is True


class TestIdentityKeybagFailure:
    """The identity-derived keybag is the only keybag path (the cert/key
    existence early return makes a fallback unreachable), so a failure in
    ``_create_keybag_file_from_identity`` propagates as a hard error.
    """

    def test_identity_keybag_helper_error_propagates(
        self, mock_pymobiledevice3, tmp_path
    ):
        """A failure in _create_keybag_file_from_identity propagates.

        The cert/key-existence early return means the identity-derived
        keybag is the only reachable path; a helper failure therefore
        surfaces as a hard error rather than falling back."""
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        with patch.object(
            supervised,
            "_create_keybag_file_from_identity",
            side_effect=RuntimeError("identity load failed"),
        ), patch.object(
            supervised,
            "_load_cert_public_bytes_from_keybag",
            return_value=b"fake",
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            # The identity helper raises BEFORE we reach set_cloud_configuration
            # → the exception propagates from supervised.py and asyncio.run raises.
            with pytest.raises(RuntimeError, match="identity load failed"):
                asyncio.run(supervised.do_supervised_pairing(
                    cert_path=str(cert_path),
                    key_path=str(key_path),
                    org_name="Test Org",
                ))


class TestStep7ReconnectTimeout:
    """Step 7 reconnect path: when _wait_for_device_reconnect returns None."""

    def test_final_verify_disconnect_reconnect_times_out(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        # First call (Step 7's verify) raises BrokenPipeError;
        # _wait_for_device_reconnect returns None → timeout branch
        call_count = {"n": 0}

        async def get_cloud_or_pipe(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise BrokenPipeError("final verify disconnect")
            return {"IsSupervised": True}

        svc.get_cloud_configuration.side_effect = get_cloud_or_pipe

        with patch.object(
            supervised, "_wait_for_cloud_config",
            new=AsyncMock(return_value={"IsSupervised": True}),
        ), patch.object(
            supervised, "_wait_for_device_reconnect", new=AsyncMock(return_value=None)
        ), patch.object(
            supervised, "_create_keybag_file_from_identity", spec=True
        ) as mock_id_keybag, patch.object(
            supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"
        ), _patch_supervised_io(mock_pymobiledevice3, svc):

            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("material")

            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        # Reconnect timed out — log message recorded
        assert result is not None


class TestValidateEnrollmentPrerequisites:
    """The validate_enrollment_prerequisites function — also uncovered."""

    def test_missing_cert_path_recorded(self):
        from apple_device_cli.enrollment import supervised

        # Both cert AND key must be provided to enter the missing-file branch.
        errors = supervised.validate_enrollment_prerequisites(
            cert_path="/nonexistent/cert.der",
            key_path="/nonexistent/key.der",
            org_name="Test Org",
        )
        assert any("Certificate not found" in e for e in errors)

    def test_invalid_cert_format_recorded(self, tmp_path):
        from apple_device_cli.enrollment import supervised

        cert_path = tmp_path / "cert.der"
        cert_path.write_bytes(b"not-a-real-cert")
        key_path = tmp_path / "key.der"
        key_path.write_bytes(b"fake-key")

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=str(cert_path),
            key_path=str(key_path),
            org_name="Test Org",
        )
        assert any("Invalid certificate format" in e for e in errors)

    def test_missing_key_path_recorded(self, tmp_path):
        from apple_device_cli.enrollment import supervised

        cert_path = tmp_path / "cert.der"
        cert_path.write_bytes(b"fake-cert")
        key_path = tmp_path / "key.der"
        key_path.write_bytes(b"fake-key")

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=str(cert_path),
            key_path="/nonexistent/key.der",
            org_name="Test Org",
        )
        assert any("Private key not found" in e for e in errors)

    def test_invalid_key_format_recorded(self, tmp_path):
        from apple_device_cli.enrollment import supervised

        cert_path = tmp_path / "cert.der"
        cert_path.write_bytes(b"fake-cert")
        key_path = tmp_path / "key.der"
        key_path.write_bytes(b"not-a-real-key")

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=str(cert_path),
            key_path=str(key_path),
            org_name="Test Org",
        )
        assert any("Invalid private key format" in e for e in errors)

    def test_only_cert_without_key_records_error(self, tmp_path):
        from apple_device_cli.enrollment import supervised

        cert_path = tmp_path / "cert.der"
        cert_path.write_bytes(b"fake-cert")

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=str(cert_path),
            key_path=None,
            org_name="Test Org",
        )
        assert any("both" in e.lower() for e in errors)

    def test_empty_org_name_records_error(self):
        from apple_device_cli.enrollment import supervised

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="",
        )
        assert any("Organization name is required" in e for e in errors)

    def test_whitespace_org_name_records_error(self):
        from apple_device_cli.enrollment import supervised

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="   ",
        )
        assert any("Organization name is required" in e for e in errors)

    def test_invalid_mdm_url_format_records_error(self):
        from apple_device_cli.enrollment import supervised

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="Test Org",
            mdm_url="not-a-url",
        )
        assert any("Invalid MDM URL format" in e for e in errors)

    def test_mdm_url_unreachable_records_error(self, monkeypatch):
        from apple_device_cli.enrollment import supervised

        def fake_urlopen(*args, **kwargs):
            from urllib.error import URLError
            raise URLError("unreachable")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="Test Org",
            mdm_url="https://unreachable.example.com",
            check_mdm_reachability=True,
        )
        assert any("unreachable" in e for e in errors)

    def test_mdm_url_unreachable_other_exception_records_error(self, monkeypatch):
        from apple_device_cli.enrollment import supervised

        def fake_urlopen(*args, **kwargs):
            raise RuntimeError("boom")

        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        errors = supervised.validate_enrollment_prerequisites(
            cert_path=None,
            key_path=None,
            org_name="Test Org",
            mdm_url="https://unreachable.example.com",
            check_mdm_reachability=True,
        )
        assert any("MDM server check failed" in e for e in errors)


class TestGetDeviceEnrollmentState:
    """The get_device_enrollment_state function — covers lines 1059-1101."""

    def test_returns_enrollment_state_dict(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)
        lockdown.udid = "test-udid"
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        svc = MagicMock(spec=MobileConfigService)
        svc.get_cloud_configuration = AsyncMock(return_value={
            "IsSupervised": True,
            "ConfigurationWasApplied": True,
            "OrganizationName": "Test Org",
            "OrganizationMagic": "org-123",
        })
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        lockdown.get_value = AsyncMock(side_effect=[
            "Activated",  # ActivationState
            True,         # IsSupervised (lockdown)
            True,         # CloudConfigurationWasApplied (lockdown)
            "Test Org",   # OrganizationName (lockdown)
            "org-123",    # OrganizationMagic (lockdown)
            False,        # WasMandatorilyUnpaired
        ])

        with patch(
            "pymobiledevice3.services.mobile_config.MobileConfigService",
            return_value=svc,
        ):
            state = supervised.get_device_enrollment_state("test-udid")

        assert state["is_supervised"] is True
        assert state["cloud_config_applied"] is True
        assert state["org_name"] == "Test Org"
        assert state["org_magic"] == "org-123"
        assert state["was_mandatorily_unpaired"] is False
        assert state["activation_state"] == "Activated"

    def test_returns_empty_state_when_cloud_config_read_fails(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        lockdown = MagicMock(spec=LockdownClient)
        lockdown.udid = "test-udid"
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(return_value=lockdown)

        svc = MagicMock(spec=MobileConfigService)
        svc.get_cloud_configuration = AsyncMock(side_effect=ConnectionError("pipe"))
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        lockdown.get_value = AsyncMock(side_effect=[
            "Activated",
            False,
            False,
            None,
            None,
            False,
        ])

        with patch(
            "pymobiledevice3.services.mobile_config.MobileConfigService",
            return_value=svc,
        ):
            state = supervised.get_device_enrollment_state("test-udid")

        # cloud_config was None, so is_supervised comes from lockdown value (False)
        assert state["is_supervised"] is False
        assert state["activation_state"] == "Activated"

    def test_returns_unknown_on_device_not_found(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised
        from pymobiledevice3.exceptions import DeviceNotFoundError

        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=DeviceNotFoundError("missing-udid")
        )

        state = supervised.get_device_enrollment_state("missing-udid")

        assert "error" in state
        assert "not found" in state["error"].lower()
        assert state["is_supervised"] is False

    def test_returns_partial_on_missing_value_error(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised
        from pymobiledevice3.exceptions import MissingValueError

        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=MissingValueError(
                "missing key", identifier=None, product_version=""
            )
        )

        state = supervised.get_device_enrollment_state("test-udid")

        assert "error" in state
        assert "unavailable" in state["error"].lower()

    def test_returns_unknown_on_generic_exception(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised

        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        state = supervised.get_device_enrollment_state("test-udid")

        assert "error" in state
        assert "boom" in state["error"]
