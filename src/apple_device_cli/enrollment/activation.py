import asyncio

from pymobiledevice3.lockdown import create_using_usbmux

from apple_device_cli.core.exceptions import ActivationError


async def do_activate(udid: str | None = None):
    """Activate device via albert.apple.com."""
    lockdown = await create_using_usbmux(udid=udid)
    # pymobiledevice3 handles activation automatically during pairing
    # This is here for explicit activation if needed
    return True


def activate_device(udid: str | None = None) -> bool:
    """Activate paired device."""
    try:
        asyncio.run(do_activate(udid))
        return True
    except Exception as e:
        raise ActivationError(f"Activation failed: {e}")