import asyncio
import tempfile
from pathlib import Path

from pymobiledevice3.ca import create_keybag_file
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.mobile_activation import MobileActivationService
from pymobiledevice3.services.mobile_config import CloudConfigurationAlreadyPresentError, MobileConfigService

from apple_device_cli.core.exceptions import EnrollmentError


async def do_supervised_pairing(
    cert_path: str | Path,
    key_path: str | Path,
    org_name: str,
    wifi_ssid: str | None = None,
    wifi_password: str | None = None,
    wifi_encryption: str = "WPA",
):
    """Perform WiFi config (if any), activation, and supervised pairing with device.

    Order follows Apple Configurator:
    1. Prepare with WiFi (optional)
    2. Activate
    3. Supervise
    """
    lockdown = await create_using_usbmux()

    async with MobileConfigService(lockdown) as svc:
        if wifi_ssid and wifi_password:
            await svc.install_wifi_profile(
                encryption_type=wifi_encryption,
                ssid=wifi_ssid,
                password=wifi_password,
            )

    if await MobileActivationService(lockdown).state() == "Unactivated":
        await MobileActivationService(lockdown).activate()

    with tempfile.TemporaryDirectory() as tmpdir:
        keybag_path = Path(tmpdir) / "keybag"
        create_keybag_file(keybag_path, org_name)

        async with MobileConfigService(lockdown) as svc:
            await svc.supervise(org_name, keybag_path)

    return await MobileConfigService(lockdown).get_cloud_configuration()


def make_supervised(
    cert_path: str | Path,
    key_path: str | Path,
    org_name: str,
    org_uuid: str | None,
    skip_list: list[str],
    mdm_url: str | None = None,
    wifi_ssid: str | None = None,
    wifi_password: str | None = None,
    wifi_encryption: str = "WPA",
) -> dict:
    """Make device supervised with cloud configuration."""
    try:
        return asyncio.run(
            do_supervised_pairing(
                cert_path, key_path, org_name, wifi_ssid, wifi_password, wifi_encryption
            )
        )
    except CloudConfigurationAlreadyPresentError:
        asyncio.run(
            _erase_device_for_reenrollment()
        )
        raise EnrollmentError(
            "Device erased for re-enrollment. Please wait for device to reboot and try again."
        )
    except Exception as e:
        raise EnrollmentError(f"Supervised pairing failed: {e}") from e


async def _erase_device_for_reenrollment():
    """Erase device cloud config to allow re-enrollment."""
    lockdown = await create_using_usbmux()
    async with MobileConfigService(lockdown) as svc:
        await svc.erase_device(preserve_data_plan=True, disallow_proximity_setup=True)
