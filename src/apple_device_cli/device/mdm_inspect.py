"""MDM inspection helpers for iOS devices.

Pure functions that take an already-connected pymobiledevice3 service and
return JSON-serializable dataclasses describing the device's MDM state.

The CLI and GUI are responsible for opening the service connection and
shutting it down cleanly. Keeping the connection lifecycle outside these
functions makes them trivially unit-testable with mock services.

Mapping to the macOS ``mdmclient`` surface:

    mdmclient QueryInstalledProfiles    -> list_profiles()
    mdmclient removeSystemProfile       -> remove_profile()
    mdmclient QueryInstalledApps        -> list_apps()
    mdmclient QueryNetworkInformation   -> get_network_info()
    mdmclient QueryCertificates         -> get_certificates()
    mdmclient QuerySecurityInfo         -> get_security_info()
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Result dataclasses (all JSON-serializable; flat, no nested unknown types)
# ---------------------------------------------------------------------------


@dataclass
class ProfileInfo:
    """A configuration profile installed on the device.

    Fields mirror the keys pymobiledevice3 returns from
    ``MobileConfigService.get_profile_list()`` under ``ProfileMetadata``.
    """

    identifier: str
    display_name: str = ""
    description: str = ""
    organization: str = ""
    payload_type: str = ""
    payload_uuid: str = ""
    payload_version: int = 0
    is_managed: bool = False  # True when the profile is MDM-managed
    is_removable: bool = True  # False when PayloadRemovalDisallowed is set
    signer_certificates: list[str] = field(default_factory=list)


@dataclass
class AppInfo:
    """An app installed on the device (sourced from installation_proxy)."""

    bundle_identifier: str
    name: str = ""
    version: str = ""
    short_version: str = ""
    application_type: str = ""  # "User", "System", etc.
    static_disk_usage: int = 0
    dynamic_disk_usage: int = 0


@dataclass
class CertificateInfo:
    """A provisioning profile installed on the device (from misagent).

    Not the same as the iOS keychain — misagent only reports
    provisioning profiles used for app development/distribution.
    """

    uuid: str
    name: str = ""
    team_identifier: str = ""
    app_id_prefix: str = ""
    expiration_date: str = ""
    raw_plist: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service protocols (just for type hints + testing; real services satisfy these)
# ---------------------------------------------------------------------------


class _AsyncMobileConfigService(Protocol):
    async def get_profile_list(self) -> dict[str, Any]: ...
    async def remove_profile(self, identifier: str) -> None: ...


class _AsyncInstallationService(Protocol):
    async def get_apps(
        self,
        application_type: str = "Any",
        calculate_sizes: bool = False,
        bundle_identifiers: list[str] | None = None,
        show_placeholders: bool = False,
    ) -> dict[str, dict[str, Any]]: ...


class _AsyncMisagentService(Protocol):
    async def copy_all(self) -> list[Any]: ...


class _AsyncDiagnosticsService(Protocol):
    async def info(self, diag_type: str = "All") -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    """Run an awaitable in a fresh event loop, matching connection.py style."""
    return asyncio.run(coro)


def _coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def list_profiles(service: Any) -> list[ProfileInfo]:
    """Return all configuration profiles installed on the device.

    :param service: a connected ``MobileConfigService`` (or any object with
        ``await get_profile_list() -> dict``).
    """
    response = _run(service.get_profile_list())
    if not response:
        return []
    metadata = response.get("ProfileMetadata") or {}
    results: list[ProfileInfo] = []
    for identifier, meta in metadata.items():
        if not isinstance(meta, dict):
            continue
        results.append(
            ProfileInfo(
                identifier=str(identifier),
                display_name=str(meta.get("PayloadDisplayName", "")),
                description=str(meta.get("PayloadDescription", "")),
                organization=str(meta.get("PayloadOrganization", "")),
                payload_type=str(meta.get("PayloadType", "")),
                payload_uuid=str(meta.get("PayloadUUID", "")),
                payload_version=_coerce_int(meta.get("PayloadVersion"), 0),
                is_managed=bool(meta.get("IsManaged", False)),
                is_removable=bool(meta.get("IsRemovable", True)),
                signer_certificates=list(meta.get("SignerCertificates") or []),
            )
        )
    # Stable order: display_name then identifier
    results.sort(key=lambda p: (p.display_name, p.identifier))
    return results


def remove_profile(service: Any, identifier: str) -> bool:
    """Remove a configuration profile by its payload identifier.

    :returns: ``True`` if the profile was present (and a removal was sent),
        ``False`` if no profiles were installed or the identifier was not
        found.  The pymobiledevice3 implementation logs and returns ``None``
        for both 'no profiles' and 'not present' cases; we differentiate
        by inspecting the current profile list first so the caller can
        tell them apart.
    :raises Exception: any error raised by the underlying service is
        propagated unchanged.
    """
    # Pre-check so we can return a meaningful True/False.  The service's
    # own remove_profile() is idempotent but does not report back whether
    # it actually removed anything.
    current = list_profiles(service)
    if not any(p.identifier == identifier for p in current):
        return False
    _run(service.remove_profile(identifier))
    return True


def list_apps(
    service: Any,
    application_type: str = "Any",
    calculate_sizes: bool = True,
) -> list[AppInfo]:
    """Return apps installed on the device.

    :param service: a connected ``InstallationProxyService``.
    :param application_type: pass-through to ``get_apps`` (``"Any"``,
        ``"User"``, ``"System"``).
    :param calculate_sizes: when ``True``, also fetch static/dynamic disk
        usage so the table can show app sizes.
    """
    apps_by_bundle = _run(
        service.get_apps(
            application_type=application_type,
            calculate_sizes=calculate_sizes,
        )
    )
    results: list[AppInfo] = []
    for bundle_id, info in apps_by_bundle.items():
        if not isinstance(info, dict):
            continue
        results.append(
            AppInfo(
                bundle_identifier=str(bundle_id),
                name=str(info.get("CFBundleName", "")),
                version=str(info.get("CFBundleVersion", "")),
                short_version=str(info.get("CFBundleShortVersionString", "")),
                application_type=str(info.get("ApplicationType", application_type)),
                static_disk_usage=_coerce_int(info.get("StaticDiskUsage"), 0),
                dynamic_disk_usage=_coerce_int(info.get("DynamicDiskUsage"), 0),
            )
        )
    results.sort(key=lambda a: a.name.lower() or a.bundle_identifier)
    return results


def get_network_info(service: Any) -> dict[str, Any]:
    """Return a flattened view of the device's network state.

    Pulls from ``DiagnosticsService.info("All")`` and projects only the
    keys the CLI/GUI actually use (Wi-Fi interface, IPv4/IPv6 addresses,
    DNS, proxy).  Returns an empty dict if the service is unavailable.
    """
    raw = _run(service.info("All"))
    if not raw:
        return {}

    wifi = raw.get("WiFi") or {}
    network = {
        "ssid": wifi.get("SSID", ""),
        "bssid": wifi.get("BSSID", ""),
        "rssi": _coerce_int(wifi.get("RSSI"), 0),
        "ipv4": raw.get("IPv4", {}).get("Addresses", []) if isinstance(raw.get("IPv4"), dict) else [],
        "ipv6": raw.get("IPv6", {}).get("Addresses", []) if isinstance(raw.get("IPv6"), dict) else [],
        "dns": raw.get("DNS", {}) if isinstance(raw.get("DNS"), dict) else {},
        "proxy": raw.get("HTTPProxy", "") or raw.get("HTTPSProxy", ""),
    }
    return network


def get_certificates(service: Any) -> list[CertificateInfo]:
    """Return provisioning profiles installed on the device.

    Note: this is **not** the iOS keychain.  ``MisagentService`` only
    reports provisioning profiles.  The function name mirrors
    ``mdmclient QueryCertificates`` for parity; the data shape is what
    misagent actually provides.
    """
    profiles = _run(service.copy_all())
    results: list[CertificateInfo] = []
    for profile in profiles:
        plist = getattr(profile, "plist", {}) or {}
        if not isinstance(plist, dict):
            continue
        # ProvisioningProfile plist: top-level keys like UUID, Name,
        # TeamIdentifier, AppIDName, ExpirationDate, etc.
        results.append(
            CertificateInfo(
                uuid=str(plist.get("UUID", "")),
                name=str(plist.get("Name", "")),
                team_identifier=str(plist.get("TeamIdentifier", "")),
                app_id_prefix=str(plist.get("AppIDPrefix", "")),
                expiration_date=str(plist.get("ExpirationDate", "")),
                raw_plist=_safe_plist_to_dict(plist),
            )
        )
    results.sort(key=lambda c: c.name or c.uuid)
    return results


def get_security_info(service: Any) -> dict[str, Any]:
    """Return a flattened view of the device's security state.

    Projects only the fields the GUI/CLI display.  Returns an empty dict
    if the diagnostics service returns nothing (older iOS versions or
    when running over a non-USB transport).
    """
    raw = _run(service.info("All"))
    if not raw:
        return {}
    # Diagnostics.info("All") returns a large dict; we expose the bits
    # the GUI/CLI actually surface.  Keep the shape flat and predictable.
    battery = raw.get("IOKitBattery") or {}
    return {
        "is_passcode_set": bool(raw.get("IsPasscodeSet", False)),
        "is_activation_lock_supported": bool(raw.get("IsActivationLockSupported", False)),
        "is_activation_lock_enabled": bool(raw.get("IsActivationLockEnabled", False)),
        "is_device_locked": bool(raw.get("IsDeviceLocked", False)),
        "device_capacity": str(raw.get("DeviceCapacity", "")),
        "device_class": str(raw.get("DeviceClass", "")),
        "model_number": str(raw.get("ModelNumber", "")),
        "serial_number": str(raw.get("SerialNumber", "")),
        "battery_current_capacity": _coerce_int(battery.get("CurrentCapacity"), 0),
        "battery_is_charging": bool(battery.get("IsCharging", False)),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_plist_to_dict(plist: Any) -> dict[str, Any]:
    """Best-effort conversion of a plist dict to a JSON-safe dict.

    Most provisioning-profile fields are already JSON-safe (str, int,
    bool, list, dict).  ``datetime`` values get stringified.  Anything
    else is replaced with its ``repr()`` so the result round-trips
    through json.dumps without raising.
    """
    import datetime

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): _walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_walk(v) for v in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="replace")
            except Exception:
                return repr(value)
        return repr(value)

    if not isinstance(plist, dict):
        return {}
    return _walk(plist)


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Convert one of our dataclasses (or a list of them) to a dict.

    Useful for the CLI's ``--json`` output path.
    """
    if isinstance(obj, list):
        return [dataclass_to_dict(o) for o in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj
