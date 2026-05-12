"""Device state detection using pymobiledevice3."""
from __future__ import annotations

import asyncio
from enum import Enum

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError


class DeviceState(Enum):
    NORMAL = "normal"
    RECOVERY = "recovery"
    DFU = "dfu"
    UNKNOWN = "unknown"


def get_device_state(udid: str) -> DeviceState:
    """Get the current state of the device."""
    async def _get():
        try:
            lockdown = await create_using_usbmux(serial=udid)
            if lockdown.all_values.get("RecoveryMode"):
                return DeviceState.RECOVERY
            return DeviceState.NORMAL
        except ConnectionFailedToUsbmuxdError:
            return DeviceState.UNKNOWN
        except Exception:
            return DeviceState.UNKNOWN
    return asyncio.run(_get())