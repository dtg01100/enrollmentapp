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
        # Try without serial first (works when only one device connected)
        try:
            lockdown = await create_using_usbmux()
            actual_udid = getattr(lockdown, 'udid', None) or udid
        except Exception:
            # Fall back to serial when multiple devices might be connected
            lockdown = await create_using_usbmux(serial=udid)
            actual_udid = udid
        vals = lockdown.all_values
        # UniqueChipID is the ECID (hex string needed by pymobiledevice3 restore)
        unique_chip_id = vals.get("UniqueChipID", "")
        ecid = hex(unique_chip_id) if isinstance(unique_chip_id, int) else str(unique_chip_id)
        return DeviceInfo(
            udid=actual_udid,
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


def get_device_info_for_recovery(udid: str, ecid: str | int | None = None) -> DeviceInfo | None:
    """Get device info from a Recovery/DFU mode device via IRecv.

    Used when the device is in Recovery mode and usbmux doesn't serve it.
    """
    from apple_device_cli.restore.erase import get_irecv

    try:
        irecv = get_irecv()
        # If ECID is provided (as hex string or int), use it to select the device
        if ecid:
            ecid_int: int
            if isinstance(ecid, str):
                ecid_int = int(ecid, 16)
            else:
                ecid_int = ecid
            dev = irecv.lookup(ecid_int)
        else:
            # Otherwise create IRecv and let it connect to whatever is in recovery
            dev = irecv
        if not dev:
            return None
        return DeviceInfo(
            udid=udid,
            device_name=getattr(dev, 'display_name', None) or "Recovery Mode Device",
            device_type=getattr(dev, 'product_type', None) or "Unknown",
            build_version=getattr(dev, 'build_version', None) or "Unknown",
            firmware_version=getattr(dev, 'product_version', None) or "Unknown",
            model=getattr(dev, 'hardware_model', None) or "",
            serial_number=getattr(dev, 'serial_number', None) or "",
            ecid=hex(getattr(dev, 'ecid', None)) if getattr(dev, 'ecid', None) else None,
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
