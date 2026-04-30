import pytest
from apple_device_cli.device.info import DeviceInfo

def test_device_info_struct():
    """DeviceInfo should be a simple dataclass."""
    info = DeviceInfo(
        udid="1234567890ABCDEF",
        device_name="iPad",
        device_type="iPad13,4",
        build_version="21A329",
        firmware_version="17.0",
    )
    assert info.udid == "1234567890ABCDEF"
    assert info.device_name == "iPad"
    assert info.ecid == ""  # default empty

def test_device_info_with_ecid():
    """DeviceInfo should accept ecid field."""
    info = DeviceInfo(
        udid="1234567890ABCDEF",
        device_name="iPad",
        device_type="iPad13,4",
        build_version="21A329",
        firmware_version="17.0",
        ecid="0xe28e921780032",
    )
    assert info.ecid == "0xe28e921780032"

def test_device_info_from_idevice_info():
    """DeviceInfo.from_idevice_info parses output correctly."""
    output = """DeviceName: Test iPad
ProductType: iPad13,4
UniqueDeviceID: ABC123456DEF
BuildVersion: 21A329
ProductVersion: 17.0
ModelNumber: J617AP
SerialNumber: ABCD12345678"""
    info = DeviceInfo.from_idevice_info(output)
    assert info.udid == "ABC123456DEF"
    assert info.device_name == "Test iPad"
    assert info.device_type == "iPad13,4"
    assert info.firmware_version == "17.0"