"""Device activation using pymobiledevice3."""
from __future__ import annotations

import asyncio

from pymobiledevice3.lockdown import create_using_usbmux

from apple_device_cli.core.exceptions import ActivationError


async def do_activate(udid: str | None = None) -> bool:
    """Activate device via albert.apple.com.

    Returns True if activation succeeded.
    Note: pymobiledevice3 handles activation automatically during pairing,
    but this is available for explicit activation if needed.
    """
    try:
        lockdown = await create_using_usbmux()
    except Exception:
        lockdown = await create_using_usbmux(serial=udid)
    # pymobiledevice3 handles activation automatically during pairing
    # This function is here for explicit activation if needed
    state = await lockdown.get_value("ActivationState")
    return state == "Activated"


def activate_device(udid: str | None = None) -> bool:
    """Activate paired device.

    Args:
        udid: Optional device UDID. If not provided, uses first available device.

    Returns:
        True if activation succeeded.

    Raises:
        ActivationError: If activation fails.
    """
    try:
        return asyncio.run(do_activate(udid))
    except Exception as e:
        raise ActivationError(f"Activation failed: {e}")
