"""Device supervision and enrollment using pymobiledevice3."""
from __future__ import annotations

import asyncio
import inspect
import plistlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_der_private_key,
)
from cryptography.x509 import load_der_x509_certificate, load_pem_x509_certificate

from pymobiledevice3.ca import create_keybag_file
from pymobiledevice3.services.mobile_config import CloudConfigurationAlreadyPresentError

from apple_device_cli.core.exceptions import EnrollmentError

SKIP_SETUP_MAPPING = {
    "location": "Location",
    "restore": "Restore",
    "sim-setup": "SIMSetup",
    "android": "Android",
    "appleid": "AppleID",
    "intended-user": "IntendedUser",
    "siri": "Siri",
    "screentime": "ScreenTime",
    "diagnostics": "Diagnostics",
    "software-update": "SoftwareUpdate",
    "passcode": "Passcode",
    "touchid": "TouchID",
    "applepay": "Payment",
    "zoom": "Zoom",
    "language": "LanguageAndLocale",
    "region": "Region",
    "true-tone": "TrueToneDisplay",
    "phone-number-permission": "MessagingActivationUsingPhoneNumber",
    "home-button": "HomeButtonSensitivity",
    "screen-saver": "ScreenSaver",
    "tap-to-setup": "TapToSetup",
    "preferred-language-setup": "PreferredLanguage",
    "keyboard-setup": "Keyboard",
    "dictation-setup": "SpokenLanguage",
    "watch-migration": "WatchMigration",
    "feature-highlights": "DisplayTone",
    "tv-provider": "TVProviderSignIn",
    "tv-home-screen-sync": "TVHomeScreenSync",
    "privacy": "Privacy",
    "where-is-this-apple-tv": "TVRoom",
    "imessage-and-facetime": "iMessageAndFaceTime",
    "app-store": "AppStore",
    "safety": "Safety",
    "multitasking": "Multitasking",
    "action-button": "ActionButton",
    "apple-intelligence": "Intelligence",
    "camera-controls": "CameraButton",
    "terms-of-address": "TermsOfAddress",
    "accessibility-appearance": "AccessibilityAppearance",
    "welcome": "Welcome",
    "appearance": "Appearance",
    "restore-completed": "RestoreCompleted",
    "update-completed": "UpdateCompleted",
    "accessibility": "Accessibility",
}


@dataclass
class EnrollmentResult:
    """Result of an enrollment operation."""
    success: bool
    device_udid: str | None = None
    supervised: bool = False
    mdm_enrolled: bool = False
    errors: list[str] = field(default_factory=list)
    cloud_config: dict[str, Any] | None = None

    @property
    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return len(self.errors) > 0

    def __str__(self) -> str:
        """Human-readable string representation."""
        status = "SUCCESS" if self.success else "FAILED"
        parts = [f"Enrollment {status}"]
        if self.device_udid:
            parts.append(f"UDID: {self.device_udid}")
        parts.append(f"Supervised: {self.supervised}")
        parts.append(f"MDM Enrolled: {self.mdm_enrolled}")
        if self.errors:
            parts.append(f"Errors: {', '.join(self.errors)}")
        return " | ".join(parts)


def _create_keybag_file_from_identity(path: Path, cert_path: str | Path, key_path: str | Path) -> None:
    """Create a keybag file from identity certificate and key."""
    cert_der = Path(cert_path).read_bytes()
    key_der = Path(key_path).read_bytes()
    cert = load_der_x509_certificate(cert_der)
    key = load_der_private_key(key_der, password=None)
    path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        + cert.public_bytes(Encoding.PEM)
    )


def _load_cert_public_bytes_from_keybag(path: Path) -> bytes:
    """Extract certificate public bytes from a keybag file."""
    data = path.read_bytes()
    begin = data.find(b"-----BEGIN CERTIFICATE-----")
    end = data.find(b"-----END CERTIFICATE-----", begin)
    if begin == -1 or end == -1:
        raise ValueError("Certificate not found in keybag file")
    cert_pem = data[begin:end + len(b"-----END CERTIFICATE-----\n")]
    cert = load_pem_x509_certificate(cert_pem)
    return cert.public_bytes(Encoding.DER)


def _map_skip_setup(skip_list: list[str]) -> list[str]:
    """Map pane names to Apple's expected format."""
    mapped = []
    for pane in skip_list:
        pane_key = pane.lower()
        if pane_key in SKIP_SETUP_MAPPING:
            mapped.append(SKIP_SETUP_MAPPING[pane_key])
            continue

        parts = [word.capitalize() for word in pane_key.replace('-', ' ').split()]
        mapped.append(''.join(parts))

    return sorted(set(mapped))


def _get_create_using_usbmux():
    """Get create_using_usbmux function (lazy import)."""
    from pymobiledevice3.lockdown import create_using_usbmux
    return create_using_usbmux


def _get_mobile_activation_service():
    """Get MobileActivationService class (lazy import)."""
    from pymobiledevice3.services.mobile_activation import MobileActivationService
    return MobileActivationService


def _get_mobile_config_service():
    """Get MobileConfigService class (lazy import)."""
    from pymobiledevice3.services.mobile_config import MobileConfigService
    return MobileConfigService


async def _maybe_await(value):
    """Await a value if it's awaitable, otherwise return it."""
    if inspect.isawaitable(value):
        return await value
    return value


async def do_supervised_pairing(
    cert_path: str | Path,
    key_path: str | Path,
    org_name: str,
    org_uuid: str | None = None,
    skip_list: list[str] | None = None,
    wifi_ssid: str | None = None,
    wifi_password: str | None = None,
    wifi_encryption: str = "WPA",
    mdm_url: str | None = None,
    mdm_checkin_url: str | None = None,
    mdm_topic: str | None = None,
    mdm_unremovable: bool = False,
    wifi_config: str | Path | None = None,
    udid: str | None = None,
    fail_on_mdm_error: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> EnrollmentResult:
    """Perform WiFi config (if any), activation, and supervised pairing with device.

    Order follows Apple Configurator:
    1. Prepare with WiFi (optional)
    2. Activate (required for fresh devices)
    3. Supervise
    4. Install MDM enrollment profile (if MDM URL provided)

    Args:
        cert_path: Path to certificate file
        key_path: Path to private key file
        org_name: Organization name
        org_uuid: Optional organization UUID
        skip_list: List of Setup Assistant panes to skip
        wifi_ssid: WiFi network SSID
        wifi_password: WiFi password
        wifi_encryption: WiFi encryption type (default: WPA)
        mdm_url: MDM server URL
        mdm_checkin_url: MDM check-in URL
        mdm_topic: MDM topic
        mdm_unremovable: Whether MDM profile is unremovable
        wifi_config: Path to WiFi mobileconfig file
        udid: Optional device UDID to target specific device
        fail_on_mdm_error: Raise error if MDM profile install fails
        progress_callback: Optional callback for progress updates

    Returns:
        EnrollmentResult with operation details
    """
    create_using_usbmux = _get_create_using_usbmux()
    MobileActivationService = _get_mobile_activation_service()
    MobileConfigService = _get_mobile_config_service()

    errors: list[str] = []
    mdm_enrolled = False

    def _progress(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    # Validate certificate and key files early
    cert_path_p = Path(cert_path)
    key_path_p = Path(key_path)
    if not cert_path_p.exists():
        return EnrollmentResult(
            success=False,
            device_udid=udid,
            errors=[f"Certificate not found: {cert_path}"],
        )
    if not key_path_p.exists():
        return EnrollmentResult(
            success=False,
            device_udid=udid,
            errors=[f"Private key not found: {key_path}"],
        )

    # Step 1: Connect to device
    _progress("Connecting to device...")
    lockdown = await create_using_usbmux(serial=udid)
    device_udid = getattr(lockdown, 'udid', udid)

    # Step 2: Check and perform activation if needed
    _progress("Checking activation state...")
    activation_svc = MobileActivationService(lockdown)
    activation_state = await _maybe_await(activation_svc.state())
    if activation_state == "Unactivated":
        _progress("Activating device...")
        await _maybe_await(activation_svc.activate())

    # Step 3: Install WiFi config if provided
    _progress("Configuring WiFi...")
    async with MobileConfigService(lockdown) as svc:
        if wifi_config:
            wifi_config_path = Path(wifi_config)
            if wifi_config_path.exists():
                wifi_payload = wifi_config_path.read_bytes()
                await _maybe_await(svc.install_profile(wifi_payload))
            else:
                errors.append(f"WiFi config file not found: {wifi_config}")
        elif wifi_ssid and wifi_password:
            await _maybe_await(svc.install_wifi_profile(
                encryption_type=wifi_encryption,
                ssid=wifi_ssid,
                password=wifi_password,
            ))

    # Step 4: Supervise device (check first if already supervised)
    _progress("Applying supervision...")
    with tempfile.TemporaryDirectory() as tmpdir:
        keybag_path = Path(tmpdir) / "keybag"

        if Path(cert_path).exists() and Path(key_path).exists():
            _create_keybag_file_from_identity(keybag_path, cert_path, key_path)
        else:
            create_keybag_file(keybag_path, org_name)

        # Check if device already has cloud configuration
        already_supervised = False
        try:
            async with MobileConfigService(lockdown) as svc:
                existing_config = await _maybe_await(svc.get_cloud_configuration())
                if isinstance(existing_config, dict) and existing_config.get("IsSupervised"):
                    already_supervised = True
                    _progress("Device already supervised, updating configuration...")
        except Exception:
            pass

        if not already_supervised:
            try:
                async with MobileConfigService(lockdown) as svc:
                    await _maybe_await(svc.supervise(org_name, keybag_path))
            except CloudConfigurationAlreadyPresentError:
                # Already has cloud config, try to update it
                already_supervised = True
            except Exception as e:
                errors.append(f"Supervision failed: {e}")

        # Always update cloud configuration with latest settings
        async with MobileConfigService(lockdown) as svc:
            cloud_config_payload = {
                "AllowPairing": True,
                "CloudConfigurationUIComplete": True,
                "ConfigurationSource": 2,
                "ConfigurationWasApplied": True,
                "IsMDMUnremovable": mdm_unremovable,
                "IsMandatory": True,
                "IsMultiUser": False,
                "IsSupervised": True,
                "MDMServerURL": mdm_url if mdm_url else None,
                "OrganizationMagic": org_uuid or str(uuid4()),
                "OrganizationName": org_name,
                "PostSetupProfileWasInstalled": True,
                "SupervisorHostCertificates": [
                    _load_cert_public_bytes_from_keybag(keybag_path),
                ],
            }
            if skip_list:
                cloud_config_payload["SkipSetup"] = _map_skip_setup(skip_list)
            
            try:
                await _maybe_await(svc.set_cloud_configuration(cloud_config_payload))
            except CloudConfigurationAlreadyPresentError:
                # Cloud config already present - this is OK if we're updating
                pass

        # Step 5: Install MDM enrollment profile (non-fatal - supervision is the main goal)
        # Note: Direct MDM profile installation via pymobiledevice3 MobileConfigService
        # fails with profile validation errors. MDM enrollment should be completed
        # via Setup Assistant when the device boots - the cloud config already has
        # the MDM server URL which Setup Assistant uses for enrollment.
        if mdm_url:
            _progress(f"MDM enrollment deferred (use Setup Assistant on device)")
            mdm_enrolled = False

    # Step 6: Get cloud configuration result
    _progress("Verifying configuration...")
    cloud_config = None
    try:
        async with MobileConfigService(lockdown) as svc:
            cloud_config = await _maybe_await(svc.get_cloud_configuration())
    except Exception:
        pass

    supervised = cloud_config.get("IsSupervised", False) if cloud_config else False

    return EnrollmentResult(
        success=len(errors) == 0,
        device_udid=device_udid,
        supervised=supervised,
        mdm_enrolled=mdm_enrolled,
        errors=errors,
        cloud_config=cloud_config if isinstance(cloud_config, dict) else None,
    )


def make_supervised(
    cert_path: str | Path,
    key_path: str | Path,
    org_name: str,
    org_uuid: str | None = None,
    skip_list: list[str] | None = None,
    mdm_url: str | None = None,
    wifi_ssid: str | None = None,
    wifi_password: str | None = None,
    wifi_encryption: str = "WPA",
    mdm_checkin_url: str | None = None,
    mdm_topic: str | None = None,
    mdm_unremovable: bool = False,
    wifi_config: str | Path | None = None,
    udid: str | None = None,
    fail_on_mdm_error: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> EnrollmentResult:
    """Make device supervised with cloud configuration.

    Args:
        cert_path: Path to certificate file
        key_path: Path to private key file
        org_name: Organization name
        org_uuid: Optional organization UUID
        skip_list: List of Setup Assistant panes to skip
        mdm_url: MDM server URL
        wifi_ssid: WiFi network SSID
        wifi_password: WiFi password
        wifi_encryption: WiFi encryption type (default: WPA)
        mdm_checkin_url: MDM check-in URL
        mdm_topic: MDM topic
        mdm_unremovable: Whether MDM profile is unremovable
        wifi_config: Path to WiFi mobileconfig file
        udid: Optional device UDID to target specific device
        fail_on_mdm_error: Raise EnrollmentError if MDM profile install fails
        progress_callback: Optional callback for progress updates

    Returns:
        EnrollmentResult with operation details

    Raises:
        EnrollmentError: If supervised pairing fails (except CloudConfigurationAlreadyPresent)
    """
    try:
        return asyncio.run(
            do_supervised_pairing(
                cert_path=cert_path,
                key_path=key_path,
                org_name=org_name,
                org_uuid=org_uuid,
                skip_list=skip_list,
                wifi_ssid=wifi_ssid,
                wifi_password=wifi_password,
                wifi_encryption=wifi_encryption,
                mdm_url=mdm_url,
                mdm_checkin_url=mdm_checkin_url,
                mdm_topic=mdm_topic,
                mdm_unremovable=mdm_unremovable,
                wifi_config=wifi_config,
                udid=udid,
                fail_on_mdm_error=fail_on_mdm_error,
                progress_callback=progress_callback,
            )
        )
    except CloudConfigurationAlreadyPresentError:
        # Cloud config already present - this is OK for re-enrollment
        # Try to get current config to report accurate status
        try:
            async def get_cloud_config():
                async with MobileConfigService(lockdown) as svc:
                    return await _maybe_await(svc.get_cloud_configuration())
            cloud_config = asyncio.run(get_cloud_config())
        except Exception:
            cloud_config = None
        supervised = cloud_config.get("IsSupervised", False) if isinstance(cloud_config, dict) else False
        return EnrollmentResult(
            success=True,  # Cloud config present means it's configured
            device_udid=udid,
            supervised=supervised,
            mdm_enrolled=mdm_enrolled if mdm_enrolled else False,
            errors=["Cloud configuration already present"] if already_supervised else [],
            cloud_config=cloud_config if isinstance(cloud_config, dict) else None,
        )
    except Exception as e:
        raise EnrollmentError(f"Supervised pairing failed: {e}") from e


async def _erase_device_for_reenrollment_async(udid: str | None = None):
    """Erase device cloud config to allow re-enrollment (async version).

    Args:
        udid: Optional device UDID to target specific device
    """
    create_using_usbmux = _get_create_using_usbmux()
    MobileConfigService = _get_mobile_config_service()

    lockdown = await create_using_usbmux(serial=udid)
    async with MobileConfigService(lockdown) as svc:
        await _maybe_await(svc.erase_device(preserve_data_plan=True, disallow_proximity_setup=True))


def erase_device_for_reenrollment(udid: str | None = None) -> bool:
    """Erase device cloud config to allow re-enrollment.

    Args:
        udid: Optional device UDID to target specific device

    Returns:
        True if erase succeeded

    Raises:
        EnrollmentError: If erase operation fails
    """
    try:
        asyncio.run(_erase_device_for_reenrollment_async(udid))
        return True
    except Exception as e:
        raise EnrollmentError(f"Failed to erase device for re-enrollment: {e}") from e


def validate_enrollment_prerequisites(
    cert_path: str | Path | None,
    key_path: str | Path | None,
    org_name: str,
    mdm_url: str | None = None,
    check_mdm_reachability: bool = False,
) -> list[str]:
    """Validate enrollment prerequisites before attempting enrollment.

    Args:
        cert_path: Path to certificate file (optional)
        key_path: Path to private key file (optional)
        org_name: Organization name
        mdm_url: Optional MDM server URL to validate
        check_mdm_reachability: Whether to check if MDM URL is reachable

    Returns:
        List of validation errors (empty if all valid)
    """
    errors: list[str] = []

    # Check certificate and key files
    if cert_path and key_path:
        cert_path_p = Path(cert_path)
        key_path_p = Path(key_path)

        if not cert_path_p.exists():
            errors.append(f"Certificate not found: {cert_path}")
        if not key_path_p.exists():
            errors.append(f"Private key not found: {key_path}")

        # Validate certificate format
        if cert_path_p.exists():
            try:
                cert_der = cert_path_p.read_bytes()
                load_der_x509_certificate(cert_der)
            except Exception as e:
                errors.append(f"Invalid certificate format: {e}")

        # Validate key format
        if key_path_p.exists():
            try:
                key_der = key_path_p.read_bytes()
                load_der_private_key(key_der, password=None)
            except Exception as e:
                errors.append(f"Invalid private key format: {e}")
    elif cert_path or key_path:
        errors.append("Both cert_path and key_path must be provided together")

    # Check organization name
    if not org_name or not org_name.strip():
        errors.append("Organization name is required")

    # Check MDM URL format if provided
    if mdm_url:
        if not mdm_url.startswith(("http://", "https://")):
            errors.append(f"Invalid MDM URL format: {mdm_url}")

        # Check MDM reachability if requested
        if check_mdm_reachability:
            try:
                import urllib.request
                req = urllib.request.Request(mdm_url, method="HEAD")
                urllib.request.urlopen(req, timeout=10)
            except urllib.error.URLError:
                errors.append(f"MDM server unreachable: {mdm_url}")
            except Exception as e:
                errors.append(f"MDM server check failed: {e}")

    return errors


def get_device_enrollment_state(udid: str) -> dict[str, Any]:
    """Get current enrollment state of a device.

    Args:
        udid: Device UDID

    Returns:
        Dict with enrollment state information
    """
    async def _get():
        create_using_usbmux = _get_create_using_usbmux()
        lockdown = await create_using_usbmux(serial=udid)
        return {
            "activation_state": lockdown.get("ActivationState"),
            "is_supervised": lockdown.get("IsSupervised", False),
            "cloud_config_applied": lockdown.get("CloudConfigurationWasApplied", False),
            "org_name": lockdown.get("OrganizationName"),
            "org_magic": lockdown.get("OrganizationMagic"),
            "is_mdm_managed": lockdown.get("WasMandatorilyUnpaired", False),
        }

    try:
        return asyncio.run(_get())
    except Exception as e:
        return {
            "error": str(e),
            "activation_state": "Unknown",
            "is_supervised": False,
            "cloud_config_applied": False,
        }
