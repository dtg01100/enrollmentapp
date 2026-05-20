"""Device connection and enumeration using pymobiledevice3."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3 import usbmux
from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError

from apple_device_cli.device.info import DeviceInfo


def list_devices() -> list[DeviceInfo]:
    """List all connected iOS devices.

    Returns an empty list when usbmuxd is not running (e.g. no normal-mode
    Apple device is connected — the daemon starts on-demand via socket
    activation and is simply absent when only Recovery/DFU devices are present).
    """
    async def _list():
        try:
            devs = await usbmux.list_devices()
        except ConnectionFailedToUsbmuxdError:
            return []
        devices = []
        for record in devs:
            udid = record.serial
            try:
                info = await asyncio.wait_for(_get_device_info_async(udid), timeout=5)
            except asyncio.TimeoutError:
                info = None
            if info:
                devices.append(info)
        return devices
    return asyncio.run(_list())


def get_device_info(udid: str) -> DeviceInfo | None:
    """Get device information for a specific UDID."""
    async def _get():
        try:
            return await asyncio.wait_for(_get_device_info_async(udid), timeout=8)
        except asyncio.TimeoutError:
            return None
    return asyncio.run(_get())


async def _get_device_info_async(udid: str) -> DeviceInfo | None:
    """Get device info using lockdown service."""
    try:
        actual_udid: str | None = None
        # Prioritize specified serial
        if udid:
            try:
                lockdown = await create_using_usbmux(serial=udid)
                actual_udid = udid
            except Exception:
                # Fall back to any device if serial-specific fails (e.g. older usbmuxd)
                lockdown = await create_using_usbmux()
                actual_udid = getattr(lockdown, "udid", udid) or udid
                if udid and actual_udid != udid:
                    return None
        else:
            lockdown = await create_using_usbmux()
            actual_udid = getattr(lockdown, "udid", None)
        vals = lockdown.all_values
        # UniqueChipID is the ECID (hex string needed by pymobiledevice3 restore)
        unique_chip_id = vals.get("UniqueChipID", "")
        ecid = hex(unique_chip_id) if isinstance(unique_chip_id, int) else str(unique_chip_id)
        return DeviceInfo(
            udid=actual_udid or "Unknown",
            device_name=vals.get("DeviceName", "Unknown"),
            device_type=vals.get("ProductType", "Unknown"),
            build_version=vals.get("BuildVersion", "Unknown"),
            firmware_version=vals.get("ProductVersion", "Unknown"),
            model=vals.get("ModelNumber", ""),
            serial_number=vals.get("SerialNumber", ""),
            ecid=ecid,
        )
    except Exception:
        return None


def ensure_device_pairing(udid: str, timeout: int = 45) -> None:
    """Ensure the host is paired with the specified device.

    This triggers `pymobiledevice3 lockdown pair --udid ...` which is fast when
    already paired, and prompts the user to trust the host when pairing is
    needed.
    """
    cmd = [sys.executable, "-m", "pymobiledevice3", "lockdown", "pair", "--udid", udid]
    try:
        result = subprocess.run(cmd, text=True, timeout=timeout, check=False, capture_output=True)
    except subprocess.TimeoutExpired:
        # Best effort only: pairing may not be required on already-trusted hosts.
        logging.warning("pairing check timed out. If a Trust prompt is visible, unlock the device and tap Trust.")
        return
    if result.returncode != 0:
        # Best effort only: proceed and let the next step surface actionable errors.
        stderr = result.stderr.strip() if result.stderr else ""
        msg = f"pairing check failed (rc={result.returncode})"
        if stderr:
            msg += f": {stderr}"
        logging.warning(f"{msg}. Continuing; if prompted on device, unlock and tap Trust.")


def wait_for_udid_in_usbmux(udid: str, timeout: int = 60, interval: float = 2.0) -> bool:
    """Wait until *udid* is visible via usbmux (normal-mode device path)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            records = asyncio.run(usbmux.list_devices())
            if any(getattr(record, "serial", None) == udid for record in records):
                return True
        except ConnectionFailedToUsbmuxdError:
            pass
        time.sleep(interval)
    return False
