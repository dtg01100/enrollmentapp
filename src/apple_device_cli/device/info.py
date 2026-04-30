from dataclasses import dataclass

@dataclass
class DeviceInfo:
    udid: str
    device_name: str
    device_type: str
    build_version: str
    firmware_version: str
    model: str = ""
    serial_number: str = ""
    ecid: str = ""

    @classmethod
    def from_idevice_info(cls, output: str) -> "DeviceInfo":
        """Parse ideviceinfo output into DeviceInfo."""
        info = {}
        for line in output.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                info[key.strip()] = value.strip()
        # UniqueChipID is the ECID; convert int to hex string if present
        unique_chip_id = info.get("UniqueChipID", "")
        ecid = ""
        if unique_chip_id:
            try:
                ecid = hex(int(unique_chip_id))
            except (ValueError, TypeError):
                ecid = str(unique_chip_id)
        return cls(
            udid=info.get("UniqueDeviceID", ""),
            device_name=info.get("DeviceName", "Unknown"),
            device_type=info.get("ProductType", "Unknown"),
            build_version=info.get("BuildVersion", "Unknown"),
            firmware_version=info.get("ProductVersion", "Unknown"),
            model=info.get("ModelNumber", ""),
            serial_number=info.get("SerialNumber", ""),
            ecid=ecid,
        )