"""Tests for device enumeration and per-device info lookup (connection.py).

Covers the two helpers at the top of ``device/connection.py`` that were
previously exercised only by integration tests:

* ``list_devices()`` — async loop that calls ``usbmux.list_devices()`` and
  wraps each returned record in a ``DeviceInfo``. Branches covered:
  - happy path: records are wrapped into DeviceInfo
  - ``ConnectionFailedToUsbmuxdError`` is swallowed and returns ``[]``
  - per-record ``asyncio.TimeoutError`` is swallowed (record skipped)
  - record missing ``serial`` is skipped (None udid → None info)
  - empty list returns ``[]``

* ``_get_device_info_async()`` — async core that builds a DeviceInfo from
  ``lockdown.all_values``. Branches covered:
  - happy path with explicit udid
  - explicit udid but ``create_using_usbmux(serial=udid)`` raises → falls
    back to ``create_using_usbmux()`` with no serial
  - explicit udid but fallback returns a different udid → ``None``
  - empty udid → uses ``create_using_usbmux()`` with no serial
  - any exception in the body is swallowed and returns ``None``

Also covers the public ``get_device_info()`` timeout branch (the public
wrapper around ``_get_device_info_async``).

The conftest autouse ``mock_pymobiledevice3`` fixture patches
``pymobiledevice3.lockdown`` and ``pymobiledevice3.services`` but does NOT
set up ``pymobiledevice3.usbmux``. Each test therefore monkeypatches
``connection.usbmux`` with a synthetic mock, mirroring the pattern in
``test_connection_helpers.py``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError
from pymobiledevice3.usbmux import MuxDevice
from pymobiledevice3.lockdown import LockdownClient

import apple_device_cli.device.connection as connection
from apple_device_cli.device.connection import (
    _get_device_info_async,
    get_device_info,
    list_devices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(serial):
    """Build a MuxDevice-shaped record with a serial attribute."""
    record = MagicMock(spec=MuxDevice)
    record.serial = serial
    return record


def _make_lockdown(
    *,
    udid: str = "UDID-ABC",
    device_name: str = "Test iPhone",
    product_type: str = "iPhone15,2",
    build_version: str = "21A123",
    product_version: str = "17.0",
    model_number: str = "MQ9G3LL/A",
    serial_number: str = "F4LX12345678",
    unique_chip_id: int | str = 0x1234567890ABCDEF,
):
    """Build a LockdownClient-shaped mock populated with all_values."""
    lockdown = MagicMock(spec=LockdownClient)
    lockdown.udid = udid
    lockdown.all_values = {
        "DeviceName": device_name,
        "ProductType": product_type,
        "BuildVersion": build_version,
        "ProductVersion": product_version,
        "ModelNumber": model_number,
        "SerialNumber": serial_number,
        "UniqueChipID": unique_chip_id,
    }
    return lockdown


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


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


class TestListDevices:
    """list_devices() branches — covers the 60% → 100% gap in connection.py."""

    def test_empty_list_returns_empty(self, monkeypatch):
        """No devices present → returns []."""
        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([])
        )
        monkeypatch.setattr(
            connection, "usbmux", MagicMock(list_devices=list_devices_mock)
        )

        assert list_devices() == []

    def test_usbmuxd_not_running_swallows_error(self, monkeypatch):
        """ConnectionFailedToUsbmuxdError is swallowed, returns []."""
        def boom():
            return _coro_that_raises(ConnectionFailedToUsbmuxdError())

        list_devices_mock = MagicMock(side_effect=boom)
        monkeypatch.setattr(
            connection, "usbmux", MagicMock(list_devices=list_devices_mock)
        )

        assert list_devices() == []

    def test_happy_path_wraps_records_in_device_info(self, monkeypatch):
        """Each record with a serial is wrapped via _get_device_info_async."""
        record_a = _make_record("UDID-A")
        record_b = _make_record("UDID-B")
        info_a = MagicMock(__class__=object)  # sentinel — assert by identity
        info_b = MagicMock(__class__=object)

        async def fake_get(udid):
            return {"UDID-A": info_a, "UDID-B": info_b}[udid]

        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([record_a, record_b])
        )
        monkeypatch.setattr(
            connection, "usbmux", MagicMock(list_devices=list_devices_mock)
        )
        monkeypatch.setattr(connection, "_get_device_info_async", fake_get)

        result = list_devices()
        assert result == [info_a, info_b]

    def test_record_without_serial_is_skipped(self, monkeypatch):
        """A record with serial=None results in udid=None → None info → skipped."""
        # A MuxDevice-shaped record whose .serial is None — the only realistic
        # shape a real device record can take when serial is missing. (Plain
        # `object()` fails because list_devices reads `record.serial` directly,
        # not via getattr; a spec'd mock is the truthful simulation.)
        bad_record = MagicMock(spec=MuxDevice)
        bad_record.serial = None
        info_a = MagicMock(name="info_a")
        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([bad_record, _make_record("UDID-A")])
        )

        async def fake_get(udid):
            # Bad record resolves to None udid; _get_device_info_async returns None
            if udid is None:
                return None
            assert udid == "UDID-A"
            return info_a

        monkeypatch.setattr(
            connection, "usbmux", MagicMock(list_devices=list_devices_mock)
        )
        monkeypatch.setattr(connection, "_get_device_info_async", fake_get)

        result = list_devices()
        assert result == [info_a]

    def test_per_record_timeout_skips_device(self, monkeypatch):
        """asyncio.TimeoutError from _get_device_info_async skips that record."""
        good = _make_record("UDID-GOOD")
        info_good = MagicMock(name="info_good")
        list_devices_mock = MagicMock(
            side_effect=lambda: _coro_that_returns([_make_record("UDID-SLOW"), good])
        )

        async def fake_get(udid):
            if udid == "UDID-SLOW":
                raise asyncio.TimeoutError  # noqa: PIE791 — intentional
            return info_good

        monkeypatch.setattr(
            connection, "usbmux", MagicMock(list_devices=list_devices_mock)
        )
        monkeypatch.setattr(connection, "_get_device_info_async", fake_get)

        result = list_devices()
        assert result == [info_good]


# ---------------------------------------------------------------------------
# get_device_info (public timeout wrapper)
# ---------------------------------------------------------------------------


class TestGetDeviceInfo:
    """get_device_info() — the public 8s-timeout wrapper around _get_device_info_async."""

    def test_happy_path_returns_device_info(self, monkeypatch):
        """Returns the DeviceInfo produced by the inner async helper."""
        sentinel = MagicMock(name="device_info")

        async def fake_inner(udid):
            assert udid == "UDID-1"
            return sentinel

        monkeypatch.setattr(connection, "_get_device_info_async", fake_inner)
        assert get_device_info("UDID-1") is sentinel

    def test_timeout_returns_none(self, monkeypatch):
        """asyncio.TimeoutError from the inner helper is swallowed → None."""
        async def fake_inner(udid):
            raise asyncio.TimeoutError  # noqa: PIE791 — intentional

        monkeypatch.setattr(connection, "_get_device_info_async", fake_inner)
        assert get_device_info("UDID-1") is None


# ---------------------------------------------------------------------------
# _get_device_info_async
# ---------------------------------------------------------------------------


class TestGetDeviceInfoAsync:
    """Direct tests of the async core that builds DeviceInfo from lockdown."""

    def test_happy_path_with_explicit_udid(self, monkeypatch):
        """create_using_usbmux(serial=udid) → all_values → DeviceInfo."""
        lockdown = _make_lockdown(udid="UDID-EXPLICIT")
        create_mock = AsyncMock(return_value=lockdown)
        monkeypatch.setattr(connection, "create_using_usbmux", create_mock)

        result = asyncio.run(_get_device_info_async("UDID-EXPLICIT"))

        # Called with the explicit serial
        create_mock.assert_awaited_once_with(serial="UDID-EXPLICIT")
        assert result is not None
        assert result.udid == "UDID-EXPLICIT"
        assert result.device_name == "Test iPhone"
        assert result.device_type == "iPhone15,2"
        assert result.build_version == "21A123"
        assert result.firmware_version == "17.0"
        assert result.model == "MQ9G3LL/A"
        assert result.serial_number == "F4LX12345678"
        # UniqueChipID is an int → hex()
        assert result.ecid == hex(0x1234567890ABCDEF)

    def test_unique_chip_id_as_string_not_hexified(self, monkeypatch):
        """Non-int UniqueChipID (e.g. str) is coerced via str(), not hex()."""
        lockdown = _make_lockdown(
            unique_chip_id="not-an-int"
        )
        monkeypatch.setattr(
            connection,
            "create_using_usbmux",
            AsyncMock(return_value=lockdown),
        )

        result = asyncio.run(_get_device_info_async("UDID-1"))
        assert result is not None
        assert result.ecid == "not-an-int"

    def test_explicit_udid_falls_back_when_serial_raises(self, monkeypatch):
        """If serial-specific lockdown fails, fall back to any-device lockdown."""
        fallback = _make_lockdown(udid="UDID-EXPLICIT")

        call_count = {"n": 0}

        async def fake_create(serial=None):
            call_count["n"] += 1
            if serial == "UDID-EXPLICIT":
                raise ConnectionFailedToUsbmuxdError()
            return fallback

        monkeypatch.setattr(connection, "create_using_usbmux", fake_create)

        result = asyncio.run(_get_device_info_async("UDID-EXPLICIT"))
        assert call_count["n"] == 2
        assert result is not None
        assert result.udid == "UDID-EXPLICIT"

    def test_explicit_udid_fallback_returns_different_udid_returns_none(
        self, monkeypatch
    ):
        """If the fallback lockdown returns a different udid, return None."""
        fallback = _make_lockdown(udid="UDID-OTHER")

        async def fake_create(serial=None):
            if serial == "UDID-EXPLICIT":
                raise ConnectionFailedToUsbmuxdError()
            return fallback

        monkeypatch.setattr(connection, "create_using_usbmux", fake_create)

        assert asyncio.run(_get_device_info_async("UDID-EXPLICIT")) is None

    def test_empty_udid_uses_singleton_lockdown(self, monkeypatch):
        """No udid → create_using_usbmux() with no serial argument."""
        lockdown = _make_lockdown(udid="UDID-DISCOVERED")
        create_mock = AsyncMock(return_value=lockdown)
        monkeypatch.setattr(connection, "create_using_usbmux", create_mock)

        result = asyncio.run(_get_device_info_async(""))

        create_mock.assert_awaited_once_with()
        assert result is not None
        assert result.udid == "UDID-DISCOVERED"

    def test_exception_in_body_returns_none(self, monkeypatch):
        """Any unexpected exception in the body is swallowed → None."""
        async def boom(**_kwargs):
            raise RuntimeError("lockdown blew up")

        monkeypatch.setattr(connection, "create_using_usbmux", boom)

        assert asyncio.run(_get_device_info_async("UDID-1")) is None

    def test_fallback_when_getattr_udid_is_none(self, monkeypatch):
        """If fallback lockdown has no .udid attribute, actual_udid falls back to udid arg."""
        fallback = MagicMock(spec=LockdownClient)
        # No .udid attribute → getattr(..., "udid", None) returns None
        del fallback.udid
        fallback.all_values = {
            "DeviceName": "X",
            "ProductType": "Y",
            "BuildVersion": "Z",
            "ProductVersion": "W",
            "ModelNumber": "",
            "SerialNumber": "",
            "UniqueChipID": 1,
        }

        async def fake_create(serial=None):
            if serial == "UDID-EXPLICIT":
                raise ConnectionFailedToUsbmuxdError()
            return fallback

        monkeypatch.setattr(connection, "create_using_usbmux", fake_create)

        result = asyncio.run(_get_device_info_async("UDID-EXPLICIT"))
        assert result is not None
        # Falls back to the passed-in udid
        assert result.udid == "UDID-EXPLICIT"
