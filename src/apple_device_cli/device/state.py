"""Device state detection using pymobiledevice3."""
from __future__ import annotations

import asyncio
from enum import Enum

from pymobiledevice3.lockdown import create_using_usbmux


class DeviceState(Enum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    DFU = "dfu"


def get_device_state(udid: str) -> DeviceState:
    """Get the current state of the device."""
    async def _get():
        try:
            lockdown = await create_using_usbmux(udid=udid)
            if lockdown.all_values.get("RecoveryMode"):
                return DeviceState.RECOVERY
            return DeviceState.NORMAL
        except Exception:
            return DeviceState.RECOVERY
    return asyncio.run(_get())