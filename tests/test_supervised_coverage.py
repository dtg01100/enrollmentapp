"""Coverage-focused tests for the remaining untested branches of supervised.py.

Fills the gaps left by test_supervised_helpers.py / test_supervised_actions.py /
test_supervised_pairing_branches.py so ``supervised.py`` clears 90%:

* ``EnrollmentResult.__str__`` / ``has_errors`` (never exercised before)
* ``_wait_for_cloud_config`` timeout + OSError-retry polling
* ``_wait_for_device_reconnect`` timeout / UDID-mismatch / any-device fallback
* ``_get_lockdown_value`` TypeError / MissingValueError / wrapped-dict forms
* ``_extract_mobileconfig_error_payload`` malformed-payload branches
* ``erase_device_for_reenrollment`` (entire async flow + wrapper)
* ``get_device_enrollment_state`` ImportError fallback
* ``do_supervised_pairing`` progress callback + generic config-error branch

Mock spec rule (AGENTS.md): every class-shaped mock uses
``MagicMock(spec=RealClass)`` — no bare ``MagicMock()``.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pymobiledevice3.exceptions import MissingValueError

from apple_device_cli.core.exceptions import EnrollmentError
from apple_device_cli.enrollment import supervised

from tests.conftest import LockdownClient, MobileConfigService
from tests.test_supervised_pairing_branches import (
    _make_der_identity,
    _patch_supervised_io,
    _wire_basic_supervised_mocks,
)


# ---------------------------------------------------------------------------
# EnrollmentResult dunders
# ---------------------------------------------------------------------------


class TestEnrollmentResultStr:
    def test_str_with_udid_and_errors(self):
        """__str__ includes UDID + error list; has_errors True."""
        result = supervised.EnrollmentResult(
            success=False,
            device_udid="UDID-1234",
            errors=["boom", "bam"],
        )
        text = str(result)
        assert "FAILED" in text
        assert "UDID: UDID-1234" in text
        assert "Errors: boom, bam" in text
        assert result.has_errors is True

    def test_str_success_minimal(self):
        """__str__ with no udid/errors omits those parts; has_errors False."""
        result = supervised.EnrollmentResult(success=True)
        text = str(result)
        assert "SUCCESS" in text
        assert "UDID:" not in text
        assert "Errors:" not in text
        assert result.has_errors is False


# ---------------------------------------------------------------------------
# _wait_for_cloud_config polling branches
# ---------------------------------------------------------------------------


def _patch_asyncio_sleep(monkeypatch):
    monkeypatch.setattr(supervised.asyncio, "sleep", AsyncMock())


def _patch_mobile_config_service(monkeypatch, svc):
    """Make _get_mobile_config_service() return a class that yields ``svc``."""
    monkeypatch.setattr(
        supervised,
        "_get_mobile_config_service",
        lambda: MagicMock(return_value=svc),
    )


class TestWaitForCloudConfig:
    def _make_svc(self, get_cloud_configuration):
        svc = MagicMock(spec=MobileConfigService)
        svc.get_cloud_configuration = AsyncMock(
            side_effect=get_cloud_configuration
        )
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)
        return svc

    def test_timeout_returns_none_when_never_supervised(
        self, monkeypatch
    ):
        """Device never reports IsSupervised → poll until deadline → None."""
        lockdown = MagicMock(spec=LockdownClient)
        svc = self._make_svc(lambda: {"IsSupervised": False})
        _patch_mobile_config_service(monkeypatch, svc)
        _patch_asyncio_sleep(monkeypatch)

        result = asyncio.run(
            supervised._wait_for_cloud_config(lockdown, timeout_ms=1)
        )
        assert result is None

    def test_oserror_retries_then_returns_config(self, monkeypatch):
        """OSError during poll is swallowed; a later poll succeeds."""
        lockdown = MagicMock(spec=LockdownClient)
        svc = self._make_svc(
            [OSError("usb hiccup"), {"IsSupervised": True}]
        )
        _patch_mobile_config_service(monkeypatch, svc)
        _patch_asyncio_sleep(monkeypatch)

        result = asyncio.run(
            supervised._wait_for_cloud_config(lockdown, timeout_ms=5000)
        )
        assert result == {"IsSupervised": True}


# ---------------------------------------------------------------------------
# _wait_for_device_reconnect branches
# ---------------------------------------------------------------------------


class TestWaitForDeviceReconnect:
    def _patch_create(self, monkeypatch, create_mock):
        monkeypatch.setattr(
            supervised, "_get_create_using_usbmux", lambda: create_mock
        )

    def test_timeout_with_udid_returns_none(self, monkeypatch):
        """Target UDID never reappears → logs retry → deadline → None."""
        create_mock = AsyncMock(side_effect=ConnectionError("not yet"))
        self._patch_create(monkeypatch, create_mock)
        _patch_asyncio_sleep(monkeypatch)

        result = asyncio.run(
            supervised._wait_for_device_reconnect(timeout_ms=1, udid="UDID-X")
        )
        assert result is None
        assert create_mock.await_count >= 1

    def test_udid_mismatch_retries_then_matches(self, monkeypatch):
        """Wrong UDID from usbmux → treated as failure → retry until match."""
        wrong = MagicMock(spec=LockdownClient)
        wrong.udid = "UDID-OTHER"
        right = MagicMock(spec=LockdownClient)
        right.udid = "UDID-X"
        create_mock = AsyncMock(side_effect=[wrong, right])
        self._patch_create(monkeypatch, create_mock)
        _patch_asyncio_sleep(monkeypatch)

        result = asyncio.run(
            supervised._wait_for_device_reconnect(timeout_ms=5000, udid="UDID-X")
        )
        assert result is right

    def test_no_udid_falls_back_to_any_device(self, monkeypatch):
        """serial-specific connect fails → any-device connect succeeds."""
        any_lockdown = MagicMock(spec=LockdownClient)
        any_lockdown.udid = "UDID-ANY"

        async def create_side_effect(**kwargs):
            if "serial" in kwargs:
                raise ConnectionError("serial connect failed")
            return any_lockdown

        create_mock = AsyncMock(side_effect=create_side_effect)
        self._patch_create(monkeypatch, create_mock)
        _patch_asyncio_sleep(monkeypatch)

        result = asyncio.run(
            supervised._wait_for_device_reconnect(timeout_ms=5000)
        )
        assert result is any_lockdown

    def test_no_udid_fallback_fails_then_times_out(self, monkeypatch):
        """Both serial and any-device connects fail → deadline → None."""
        create_mock = AsyncMock(side_effect=ConnectionError("all down"))
        self._patch_create(monkeypatch, create_mock)
        _patch_asyncio_sleep(monkeypatch)

        result = asyncio.run(
            supervised._wait_for_device_reconnect(timeout_ms=1)
        )
        assert result is None


# ---------------------------------------------------------------------------
# _get_lockdown_value conventions
# ---------------------------------------------------------------------------


class TestGetLockdownValue:
    def test_typeerror_falls_back_to_keyword_form(self):
        """get_value(None, key) TypeError → get_value(key=key)."""
        lockdown = MagicMock(spec=LockdownClient)
        lockdown.get_value = MagicMock(
            side_effect=[TypeError("unsupported"), "the-value"]
        )
        result = asyncio.run(
            supervised._get_lockdown_value(lockdown, "ActivationState")
        )
        assert result == "the-value"

    def test_missing_value_returns_none(self):
        """MissingValueError while awaiting → None."""
        lockdown = MagicMock(spec=LockdownClient)

        async def raiser(*args, **kwargs):
            raise MissingValueError("missing key", identifier=None, product_version="")

        lockdown.get_value = MagicMock(return_value=raiser())
        result = asyncio.run(
            supervised._get_lockdown_value(lockdown, "ActivationState")
        )
        assert result is None

    def test_wrapped_single_key_dict_unwrapped(self):
        """A one-key {'Value': x} dict is unwrapped to x."""
        lockdown = MagicMock(spec=LockdownClient)
        lockdown.get_value = MagicMock(return_value={"Value": "inner"})
        result = asyncio.run(
            supervised._get_lockdown_value(lockdown, "ActivationState")
        )
        assert result == "inner"


# ---------------------------------------------------------------------------
# mobileconfig error payload parsing branches
# ---------------------------------------------------------------------------


class TestMobileconfigErrorParsing:
    def test_extract_no_braces_returns_none(self):
        """'ErrorChain' text without braces → None."""
        error = Exception("ErrorChain mentioned but no braces here")
        assert supervised._extract_mobileconfig_error_payload(error) is None

    def test_extract_literal_eval_failure_returns_none(self):
        """Braces present but body is not valid Python literal → None."""
        error = Exception("ErrorChain {not: valid, python}")
        assert supervised._extract_mobileconfig_error_payload(error) is None

    def test_format_skips_non_dict_chain_items(self):
        """Non-dict ErrorChain entries are skipped; dict entries surface."""
        error = Exception(
            str({"ErrorChain": [{"LocalizedDescription": "profile rejected"}, "junk"]})
        )
        formatted = supervised._format_mobileconfig_error("Install", error)
        assert formatted == "Install: profile rejected"

    def test_transient_skips_non_dict_chain_items(self):
        """Non-dict ErrorChain entries skipped; network text still detected."""
        error = Exception(
            str({"ErrorChain": ["junk", {"LocalizedDescription": "network error"}]})
        )
        assert supervised._is_transient_mobileconfig_network_error(error) is True


# ---------------------------------------------------------------------------
# erase_device_for_reenrollment (full async flow + wrapper)
# ---------------------------------------------------------------------------


class TestEraseForReenrollment:
    def _setup(self, monkeypatch, create_side_effect):
        create_mock = AsyncMock(side_effect=create_side_effect)
        monkeypatch.setattr(
            supervised, "_get_create_using_usbmux", lambda: create_mock
        )
        svc = MagicMock(spec=MobileConfigService)
        svc.erase_device = AsyncMock()
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)
        _patch_mobile_config_service(monkeypatch, svc)
        _patch_asyncio_sleep(monkeypatch)
        return create_mock, svc

    def test_happy_path_with_udid_returns_true(self, monkeypatch):
        """udid connect + erase + immediate reconnect → True."""
        lockdown = MagicMock(spec=LockdownClient)
        lockdown.udid = "UDID-1"
        create_mock, svc = self._setup(monkeypatch, lambda *a, **kw: lockdown)

        assert supervised.erase_device_for_reenrollment("UDID-1") is True
        svc.erase_device.assert_awaited_once_with(
            preserve_data_plan=True, disallow_proximity_setup=True
        )
        # First call was the pair-retry connect, rest the reconnect loop.
        assert create_mock.await_count >= 2

    def test_no_udid_connects_any_device(self, monkeypatch):
        """udid=None → create_using_usbmux() with no args → True."""
        lockdown = MagicMock(spec=LockdownClient)
        call_log = []

        async def create_side_effect(*args, **kwargs):
            call_log.append(kwargs)
            return lockdown

        self._setup(monkeypatch, create_side_effect)
        assert supervised.erase_device_for_reenrollment(None) is True
        # No serial kwarg on the first (any-device) connect.
        assert call_log[0] == {}

    def test_reconnect_failure_raises_enrollment_error(self, monkeypatch):
        """Device never reappears after erase → EnrollmentError."""
        call_log = []

        async def create_side_effect(*args, **kwargs):
            call_log.append(kwargs)
            if len(call_log) == 1:
                return MagicMock(spec=LockdownClient)
            raise ConnectionError("gone")

        self._setup(monkeypatch, create_side_effect)
        with pytest.raises(EnrollmentError, match="Failed to erase device for re-enrollment"):
            supervised.erase_device_for_reenrollment("UDID-1")

    def test_no_udid_connect_failure_propagates(self, monkeypatch):
        """udid=None + any-device connect fails → wrapper raises (1107-1108)."""
        self._setup(monkeypatch, lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("down")))
        with pytest.raises(EnrollmentError, match="Failed to erase device for re-enrollment"):
            supervised.erase_device_for_reenrollment(None)


# ---------------------------------------------------------------------------
# get_device_enrollment_state ImportError fallback
# ---------------------------------------------------------------------------


class TestGetDeviceEnrollmentStateImportError:
    def test_exceptions_module_unavailable_returns_partial(self, monkeypatch):
        """pymobiledevice3.exceptions import fails → generic partial state."""
        monkeypatch.setitem(sys.modules, "pymobiledevice3.exceptions", None)
        state = supervised.get_device_enrollment_state("test-udid")
        assert state["error"] == "pymobiledevice3.exceptions unavailable"
        assert state["is_supervised"] is False
        assert state["activation_state"] == "Unknown"


# ---------------------------------------------------------------------------
# do_supervised_pairing wiring branches (integration)
# ---------------------------------------------------------------------------


class TestDoSupervisedPairingWiring:
    def _keybag_patches(self):
        """Patches that write a real keybag file (existing test convention)."""
        return (
            patch.object(supervised, "create_keybag_file", spec=True),
            patch.object(supervised, "_create_keybag_file_from_identity", spec=True),
            patch.object(supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"),
        )

    def test_happy_path_invokes_progress_callback(self, mock_pymobiledevice3, tmp_path):
        """progress_callback receives sanitized progress lines (line 624)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)
        msgs: list[str] = []

        with self._keybag_patches()[0] as mock_keybag, self._keybag_patches()[1] as mock_id_keybag, self._keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            def make_fake(path, *_args, **_kwargs):
                path.write_text("material")

            mock_keybag.side_effect = make_fake
            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                progress_callback=msgs.append,
            ))

        assert result.success
        assert any("Connecting to device" in m for m in msgs)
        assert any("Applying supervision" in m for m in msgs)

    def test_no_udid_connect_error_propagates(self, mock_pymobiledevice3, tmp_path):
        """No udid + connect fails → ConnectionError propagates (lines 674-675)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=ConnectionError("no device")
        )

        with pytest.raises(ConnectionError):
            asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

    def test_cloud_config_generic_error_appended(self, mock_pymobiledevice3, tmp_path):
        """set_cloud_configuration non-transport failure → recorded error (777)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(
            mock_pymobiledevice3,
            set_cloud_configuration_side_effect=RuntimeError("boom"),
        )

        with self._keybag_patches()[0] as mock_keybag, self._keybag_patches()[1] as mock_id_keybag, self._keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            def make_fake(path, *_args, **_kwargs):
                path.write_text("material")

            mock_keybag.side_effect = make_fake
            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
            ))

        assert result.success is False
        assert any("Failed to configure" in e for e in result.errors)

    def test_wifi_config_missing_file_recorded(self, mock_pymobiledevice3, tmp_path):
        """wifi_config pointing at a missing file → 'not found' error (888-889)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)

        with self._keybag_patches()[0] as mock_keybag, self._keybag_patches()[1] as mock_id_keybag, self._keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            def make_fake(path, *_args, **_kwargs):
                path.write_text("material")

            mock_keybag.side_effect = make_fake
            mock_id_keybag.side_effect = make_fake

            missing = tmp_path / "nope.mobileconfig"
            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                wifi_config=str(missing),
            ))

        assert any("WiFi config file not found" in e for e in result.errors)

    def _no_keybag_patches(self):
        """Patches that create NO keybag file (keybag_path.exists() is False)."""
        return (
            patch.object(supervised, "create_keybag_file", spec=True),
            patch.object(supervised, "_create_keybag_file_from_identity", spec=True),
            patch.object(supervised, "_load_cert_public_bytes_from_keybag", return_value=b"fake"),
        )

    def test_wifi_uses_install_wifi_profile_without_keybag(self, mock_pymobiledevice3, tmp_path):
        """No keybag → install_wifi_profile fallback (845-846)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)
        svc.install_wifi_profile = AsyncMock()

        with self._no_keybag_patches()[0], self._no_keybag_patches()[1], self._no_keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                wifi_ssid="TestNet",
                wifi_password="secret",
            ))

        assert result.success
        assert result.wifi_installed is True
        svc.install_wifi_profile.assert_awaited_once()

    def test_wifi_install_failure_recorded(self, mock_pymobiledevice3, tmp_path):
        """install_wifi_profile raises → error recorded (853-855)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)
        svc.install_wifi_profile = AsyncMock(side_effect=RuntimeError("wifi failed"))

        with self._no_keybag_patches()[0], self._no_keybag_patches()[1], self._no_keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                wifi_ssid="TestNet",
                wifi_password="secret",
            ))

        assert result.success is False
        assert any("WiFi profile install failed" in e for e in result.errors)

    def test_mdm_uses_store_profile_without_keybag(self, mock_pymobiledevice3, tmp_path):
        """No keybag → MDM stored via store_profile for Setup Assistant (912-914)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)
        svc.store_profile = AsyncMock()
        mdm = tmp_path / "mdm.mobileconfig"
        mdm.write_bytes(b"<plist version='1.0'/>")

        with self._no_keybag_patches()[0], self._no_keybag_patches()[1], self._no_keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                mdm_url="https://mdm.example.com/mdm",
                mdm_mobileconfig=str(mdm),
            ))

        assert result.success
        assert result.mdm_enrolled is True
        svc.store_profile.assert_awaited_once()

    def test_step7_reconnect_verification_success(self, mock_pymobiledevice3, tmp_path):
        """Step 7 verify hits a broken pipe, reconnects, and re-verifies (978-979)."""
        cert_path, key_path = _make_der_identity(tmp_path)
        lockdown, svc = _wire_basic_supervised_mocks(mock_pymobiledevice3)
        svc.get_cloud_configuration = AsyncMock(side_effect=[
            {"IsSupervised": True},   # Step 3 wait-for-config poll
            BrokenPipeError("gone"),  # Step 7 first verify attempt
            {"IsSupervised": True},   # Step 7 reconnect verify
        ])
        fresh = MagicMock(spec=LockdownClient)
        msgs: list[str] = []

        with patch.object(
            supervised, "_wait_for_device_reconnect", new=AsyncMock(return_value=fresh)
        ), self._keybag_patches()[0] as mock_keybag, self._keybag_patches()[1] as mock_id_keybag, self._keybag_patches()[2], _patch_supervised_io(mock_pymobiledevice3, svc):
            def make_fake(path, *_args, **_kwargs):
                path.write_text("material")

            mock_keybag.side_effect = make_fake
            mock_id_keybag.side_effect = make_fake

            result = asyncio.run(supervised.do_supervised_pairing(
                cert_path=str(cert_path),
                key_path=str(key_path),
                org_name="Test Org",
                progress_callback=msgs.append,
            ))

        assert result.success
        assert result.supervised is True
        assert any("Device reconnected, configuration verified" in m for m in msgs)
