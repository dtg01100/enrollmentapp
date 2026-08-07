"""Tests for device connection helpers (pairing + usbmux polling).

Exercises the three untested branches of ``ensure_device_pairing``
(success, TimeoutExpired, non-zero rc with/without stderr) and the
four behaviors of ``wait_for_udid_in_usbmux`` (found, timeout,
usbmuxd down, record-without-serial).
"""
from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock, patch

from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError
from pymobiledevice3.usbmux import MuxDevice

import apple_device_cli.device.connection as connection
from apple_device_cli.device.connection import (
    ensure_device_pairing,
    wait_for_udid_in_usbmux,
)


# ---------------------------------------------------------------------------
# ensure_device_pairing
# ---------------------------------------------------------------------------


class TestEnsureDevicePairing:
    """All four code paths through the pairing subprocess wrapper."""

    def test_happy_path_returns_none_no_warning(self, caplog):
        """rc=0 means we silently return None — no warning logged."""
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.returncode = 0
        completed.stderr = ""

        with patch.object(subprocess, "run", return_value=completed) as run_mock:
            with caplog.at_level("WARNING"):
                result = ensure_device_pairing("UDID-1234")

        assert result is None
        assert caplog.records == []
        # Sanity: the wrapper called out to pymobiledevice3 lockdown pair
        args, _kwargs = run_mock.call_args
        cmd = args[0]
        assert cmd[:3] == [__import__("sys").executable, "-m", "pymobiledevice3"]
        assert cmd[3:] == ["lockdown", "pair", "--udid", "UDID-1234"]

    def test_timeout_expired_logs_warning(self, caplog):
        """subprocess.TimeoutExpired is swallowed and logged as a warning."""
        with patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=45),
        ):
            with caplog.at_level("WARNING"):
                result = ensure_device_pairing("UDID-1234")

        assert result is None
        assert len(caplog.records) == 1
        msg = caplog.records[0].getMessage()
        assert "timed out" in msg.lower()

    def test_non_zero_rc_with_stderr_logs_warning(self, caplog):
        """Non-zero rc with stderr surfaces the stderr in the warning."""
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.returncode = 1
        completed.stderr = "boom\n"

        with patch.object(subprocess, "run", return_value=completed):
            with caplog.at_level("WARNING"):
                result = ensure_device_pairing("UDID-1234")

        assert result is None
        assert len(caplog.records) == 1
        msg = caplog.records[0].getMessage()
        assert "rc=1" in msg
        assert "boom" in msg

    def test_non_zero_rc_empty_stderr_logs_warning(self, caplog):
        """Non-zero rc with empty stderr still logs but without stderr text."""
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.returncode = 1
        completed.stderr = ""

        with patch.object(subprocess, "run", return_value=completed):
            with caplog.at_level("WARNING"):
                result = ensure_device_pairing("UDID-1234")

        assert result is None
        assert len(caplog.records) == 1
        msg = caplog.records[0].getMessage()
        assert "rc=1" in msg
        # No colon-delimited stderr suffix when stderr is empty
        assert not msg.endswith(":")


# ---------------------------------------------------------------------------
# wait_for_udid_in_usbmux
# ---------------------------------------------------------------------------


def _make_record(serial):
    """Build a MuxDevice-shaped record with a serial attribute."""
    record = MagicMock(spec=MuxDevice)
    record.serial = serial
    return record


class TestWaitForUdidInUsbmux:
    """Polling loop branches in wait_for_udid_in_usbmux."""

    def test_found_on_first_poll_returns_true(self, monkeypatch):
        """If the very first poll sees the udid, return True immediately."""
        record = _make_record("UDID-ABC")
        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([record])
        )

        monkeypatch.setattr(connection, "usbmux", MagicMock(list_devices=list_devices_mock))
        # Sleep would block forever — make it a no-op so the test is fast.
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)

        assert wait_for_udid_in_usbmux("UDID-ABC", timeout=5) is True

    def test_never_seen_times_out(self, monkeypatch):
        """Empty list every poll → returns False once the deadline elapses."""
        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([])
        )

        monkeypatch.setattr(connection, "usbmux", MagicMock(list_devices=list_devices_mock))
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)

        assert wait_for_udid_in_usbmux("UDID-MISSING", timeout=0) is False

    def test_usbmux_not_running_swallows_error_and_returns_false(
        self, monkeypatch
    ):
        """ConnectionFailedToUsbmuxdError is swallowed; loop ends in False."""
        def boom():
            return _coro_that_raises(ConnectionFailedToUsbmuxdError())

        list_devices_mock = MagicMock(side_effect=boom)

        monkeypatch.setattr(connection, "usbmux", MagicMock(list_devices=list_devices_mock))
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)

        assert wait_for_udid_in_usbmux("UDID-XYZ", timeout=0) is False

    def test_record_without_serial_is_skipped(self, monkeypatch):
        """A record that has no ``serial`` attribute is ignored (no match)."""
        # Record missing `serial` entirely — getattr(..., 'serial', None) → None
        bad_record = object()
        good_record = _make_record("UDID-ABC")
        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([bad_record, good_record])
        )

        monkeypatch.setattr(connection, "usbmux", MagicMock(list_devices=list_devices_mock))
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)

        assert wait_for_udid_in_usbmux("UDID-ABC", timeout=5) is True


# ---------------------------------------------------------------------------
# Helpers for the usbmux polling tests
# ---------------------------------------------------------------------------


def _coro_that_returns(value):
    """Return an awaitable that resolves to ``value`` (sync)."""
    async def _coro():
        return value
    return _coro()


def _coro_that_raises(exc):
    """Return an awaitable that raises ``exc`` when awaited (sync)."""
    async def _coro():
        raise exc
    return _coro()
