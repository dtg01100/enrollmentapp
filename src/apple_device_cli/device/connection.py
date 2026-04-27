"""Device connection and enumeration using pymobiledevice3."""
from __future__ import annotations

import asyncio

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3 import usbmux

from apple_device_cli.device.info import DeviceInfo


def list_devices() -> list[DeviceInfo]:
    """List all connected iOS devices."""
    async def _list():
        devs = await usbmux.list_devices()
        devices = []
        for record in devs:
            udid = record.serial
            info = await _get_device_info_async(udid)
            if info:
                devices.append(info)
        return devices
    return asyncio.run(_list())


def get_device_info(udid: str) -> DeviceInfo | None:
    """Get device information for a specific UDID."""
    async def _get():
        return await _get_device_info_async(udid)
    return asyncio.run(_get())


async def _get_device_info_async(udid: str) -> DeviceInfo | None:
    """Get device info using lockdown service."""
    try:
        lockdown = await create_using_usbmux(serial=udid)
        vals = lockdown.all_values
        return DeviceInfo(
            udid=udid,
            device_name=vals.get("DeviceName", "Unknown"),
            device_type=vals.get("ProductType", "Unknown"),
            build_version=vals.get("BuildVersion", "Unknown"),
            firmware_version=vals.get("ProductVersion", "Unknown"),
            model=vals.get("ModelNumber", ""),
            serial_number=vals.get("SerialNumber", ""),
        )
    except Exception:
        return None