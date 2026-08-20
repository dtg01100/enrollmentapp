"""Unit tests for apple_device_cli.device.mdm_inspect.

All tests use mock service objects so they run without a real device.
Each function under test takes an already-connected service (dependency
injection) so the connection lifecycle is exercised elsewhere.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from apple_device_cli.device.mdm_inspect import (
    AppInfo,
    CertificateInfo,
    ProfileInfo,
    dataclass_to_dict,
    get_certificates,
    get_network_info,
    get_security_info,
    list_apps,
    list_profiles,
    remove_profile,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_async_mock(coro_value: Any) -> MagicMock:
    """Create a service mock whose awaitable methods return ``coro_value``.

    Returns a regular ``MagicMock`` and sets ``get_profile_list`` to an
    ``AsyncMock`` with a side-effect coroutine.  Setting the attribute
    directly (instead of relying on AsyncMock's auto-attribute behavior)
    avoids the issue where accessing an attribute on an AsyncMock would
    create a *new* AsyncMock with a different side_effect chain.
    """

    async def _coro(*_args: Any, **_kwargs: Any) -> Any:
        return coro_value

    service = MagicMock()
    service.get_profile_list = AsyncMock(side_effect=_coro)
    return service


# ---------------------------------------------------------------------------
# list_profiles
# ---------------------------------------------------------------------------


def test_list_profiles_parses_metadata():
    service = _make_async_mock(
        {
            "ProfileMetadata": {
                "com.example.mdm": {
                    "PayloadDisplayName": "Work MDM",
                    "PayloadDescription": "Managed by Acme",
                    "PayloadOrganization": "Acme Corp",
                    "PayloadType": "Configuration",
                    "PayloadUUID": "11111111-1111-1111-1111-111111111111",
                    "PayloadVersion": 1,
                    "IsManaged": True,
                    "IsRemovable": False,
                    "SignerCertificates": ["AAAA"],
                },
                "com.example.wifi": {
                    "PayloadDisplayName": "Office WiFi",
                    "PayloadDescription": "",
                    "PayloadOrganization": "",
                    "PayloadType": "com.apple.wifi.managed",
                    "PayloadUUID": "22222222-2222-2222-2222-222222222222",
                    "PayloadVersion": 1,
                    "IsManaged": True,
                    "IsRemovable": True,
                    "SignerCertificates": [],
                },
            }
        }
    )
    profiles = list_profiles(service)
    assert len(profiles) == 2
    by_id = {p.identifier: p for p in profiles}
    mdm = by_id["com.example.mdm"]
    assert mdm.display_name == "Work MDM"
    assert mdm.organization == "Acme Corp"
    assert mdm.is_managed is True
    assert mdm.is_removable is False
    assert mdm.signer_certificates == ["AAAA"]
    # Sorted by display_name -> "Office WiFi" should be first
    assert profiles[0].identifier == "com.example.wifi"


def test_list_profiles_empty_response():
    service = _make_async_mock({})
    assert list_profiles(service) == []


def test_list_profiles_none_response():
    service = _make_async_mock(None)
    assert list_profiles(service) == []


def test_list_profiles_ignores_non_dict_metadata_entries():
    service = _make_async_mock(
        {"ProfileMetadata": {"good": {"PayloadDisplayName": "X"}, "bad": "not a dict"}}
    )
    profiles = list_profiles(service)
    assert len(profiles) == 1
    assert profiles[0].identifier == "good"


# ---------------------------------------------------------------------------
# remove_profile
# ---------------------------------------------------------------------------


def test_remove_profile_returns_true_when_present():
    service = AsyncMock()
    service.get_profile_list = AsyncMock(
        return_value={"ProfileMetadata": {"com.example.mdm": {"PayloadUUID": "u"}}}
    )
    service.remove_profile = AsyncMock(return_value=None)
    assert remove_profile(service, "com.example.mdm") is True
    service.remove_profile.assert_awaited_once_with("com.example.mdm")


def test_remove_profile_returns_false_when_absent():
    service = AsyncMock()
    service.get_profile_list = AsyncMock(return_value={"ProfileMetadata": {}})
    service.remove_profile = AsyncMock(return_value=None)
    assert remove_profile(service, "com.example.nope") is False
    service.remove_profile.assert_not_called()


def test_remove_profile_returns_false_when_no_profiles():
    service = AsyncMock()
    service.get_profile_list = AsyncMock(return_value={})
    service.remove_profile = AsyncMock(return_value=None)
    assert remove_profile(service, "com.example.any") is False


# ---------------------------------------------------------------------------
# list_apps
# ---------------------------------------------------------------------------


def test_list_apps_normalizes_fields():
    service = AsyncMock()
    service.get_apps = AsyncMock(
        return_value={
            "com.example.app": {
                "CFBundleName": "Example",
                "CFBundleVersion": "42",
                "CFBundleShortVersionString": "1.2.3",
                "ApplicationType": "User",
                "StaticDiskUsage": 1024,
                "DynamicDiskUsage": 2048,
            },
            "com.apple.springboard": {
                "CFBundleName": "SpringBoard",
                "CFBundleVersion": "1",
                "ApplicationType": "System",
            },
        }
    )
    apps = list_apps(service)
    assert len(apps) == 2
    by_bundle = {a.bundle_identifier: a for a in apps}
    example = by_bundle["com.example.app"]
    assert example.name == "Example"
    assert example.version == "42"
    assert example.short_version == "1.2.3"
    assert example.application_type == "User"
    assert example.static_disk_usage == 1024
    assert example.dynamic_disk_usage == 2048
    springboard = by_bundle["com.apple.springboard"]
    assert springboard.static_disk_usage == 0  # default


def test_list_apps_forwards_application_type_and_sizes():
    service = AsyncMock()
    service.get_apps = AsyncMock(return_value={})
    list_apps(service, application_type="System", calculate_sizes=False)
    service.get_apps.assert_awaited_once_with(
        application_type="System", calculate_sizes=False
    )


def test_list_apps_ignores_non_dict_entries():
    service = AsyncMock()
    service.get_apps = AsyncMock(
        return_value={"com.example.app": {"CFBundleName": "OK"}, "broken": "not a dict"}
    )
    apps = list_apps(service)
    assert len(apps) == 1
    assert apps[0].bundle_identifier == "com.example.app"


# ---------------------------------------------------------------------------
# get_network_info
# ---------------------------------------------------------------------------


def test_get_network_info_flattens_diagnostics_response():
    service = AsyncMock()
    service.info = AsyncMock(
        return_value={
            "WiFi": {"SSID": "AcmeCorp", "BSSID": "00:11:22:33:44:55", "RSSI": -55},
            "IPv4": {"Addresses": ["10.0.0.42"]},
            "IPv6": {"Addresses": ["fe80::1"]},
            "DNS": {"Servers": ["10.0.0.1"]},
            "HTTPProxy": "",
        }
    )
    info = get_network_info(service)
    assert info["ssid"] == "AcmeCorp"
    assert info["bssid"] == "00:11:22:33:44:55"
    assert info["rssi"] == -55
    assert info["ipv4"] == ["10.0.0.42"]
    assert info["ipv6"] == ["fe80::1"]
    assert info["dns"] == {"Servers": ["10.0.0.1"]}
    assert info["proxy"] == ""


def test_get_network_info_empty_when_service_returns_none():
    service = AsyncMock()
    service.info = AsyncMock(return_value=None)
    assert get_network_info(service) == {}


def test_get_network_info_empty_when_service_returns_empty():
    service = AsyncMock()
    service.info = AsyncMock(return_value={})
    assert get_network_info(service) == {}


# ---------------------------------------------------------------------------
# get_certificates
# ---------------------------------------------------------------------------


class _FakeProfile:
    def __init__(self, plist: dict[str, Any]):
        self.plist = plist


def test_get_certificates_normalizes_provisioning_profiles():
    service = AsyncMock()
    service.copy_all = AsyncMock(
        return_value=[
            _FakeProfile(
                {
                    "UUID": "11111111-1111-1111-1111-111111111111",
                    "Name": "Acme Dev Profile",
                    "TeamIdentifier": ["ABCDE12345"],
                    "AppIDPrefix": ["ABCDE12345.com.example."],
                    "ExpirationDate": "2030-01-01T00:00:00Z",
                }
            )
        ]
    )
    certs = get_certificates(service)
    assert len(certs) == 1
    c = certs[0]
    assert c.uuid == "11111111-1111-1111-1111-111111111111"
    assert c.name == "Acme Dev Profile"
    assert c.team_identifier == "['ABCDE12345']"  # str() of a list
    assert c.expiration_date == "2030-01-01T00:00:00Z"


def test_get_certificates_empty():
    service = AsyncMock()
    service.copy_all = AsyncMock(return_value=[])
    assert get_certificates(service) == []


def test_get_certificates_handles_non_dict_plist():
    service = AsyncMock()
    service.copy_all = AsyncMock(
        return_value=[_FakeProfile("not a dict"), _FakeProfile({"UUID": "u", "Name": "n"})]
    )
    certs = get_certificates(service)
    assert len(certs) == 1
    assert certs[0].uuid == "u"


# ---------------------------------------------------------------------------
# get_security_info
# ---------------------------------------------------------------------------


def test_get_security_info_flattens_diagnostics_response():
    service = AsyncMock()
    service.info = AsyncMock(
        return_value={
            "IsPasscodeSet": True,
            "IsActivationLockSupported": True,
            "IsActivationLockEnabled": False,
            "IsDeviceLocked": False,
            "DeviceCapacity": "64GB",
            "DeviceClass": "iPhone",
            "ModelNumber": "MQ8L2LL/A",
            "SerialNumber": "F2LXXXXXQ1G7",
            "IOKitBattery": {"CurrentCapacity": 87, "IsCharging": True},
        }
    )
    sec = get_security_info(service)
    assert sec["is_passcode_set"] is True
    assert sec["is_activation_lock_supported"] is True
    assert sec["is_activation_lock_enabled"] is False
    assert sec["device_class"] == "iPhone"
    assert sec["battery_current_capacity"] == 87
    assert sec["battery_is_charging"] is True


def test_get_security_info_empty_when_service_returns_none():
    service = AsyncMock()
    service.info = AsyncMock(return_value=None)
    assert get_security_info(service) == {}


def test_get_security_info_missing_battery_block():
    service = AsyncMock()
    service.info = AsyncMock(return_value={"IsPasscodeSet": True})
    sec = get_security_info(service)
    assert sec["is_passcode_set"] is True
    assert sec["battery_current_capacity"] == 0
    assert sec["battery_is_charging"] is False


# ---------------------------------------------------------------------------
# JSON-serializability (covers CLI --json path)
# ---------------------------------------------------------------------------


def test_profile_info_is_json_serializable():
    p = ProfileInfo(identifier="x", display_name="X", payload_version=1)
    payload = json.dumps(dataclass_to_dict(p))
    assert json.loads(payload)["identifier"] == "x"


def test_app_info_is_json_serializable():
    a = AppInfo(bundle_identifier="com.x", name="X", static_disk_usage=100)
    payload = json.dumps(dataclass_to_dict(a))
    assert json.loads(payload)["bundle_identifier"] == "com.x"


def test_certificate_info_is_json_serializable():
    c = CertificateInfo(uuid="u", name="n", team_identifier="t")
    payload = json.dumps(dataclass_to_dict(c))
    assert json.loads(payload)["name"] == "n"


def test_dataclass_to_dict_handles_list_of_dataclasses():
    apps = [
        AppInfo(bundle_identifier="a", name="A"),
        AppInfo(bundle_identifier="b", name="B"),
    ]
    payload = json.dumps(dataclass_to_dict(apps))
    parsed = json.loads(payload)
    assert len(parsed) == 2
    assert parsed[0]["bundle_identifier"] == "a"
    assert parsed[1]["bundle_identifier"] == "b"
