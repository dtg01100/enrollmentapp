"""Custom exceptions for apple_device_cli."""


class AppleDeviceError(Exception):
    """Base exception for all Apple device-related errors."""
    pass


class DeviceNotFoundError(AppleDeviceError):
    """Raised when a device cannot be found or connected."""
    pass


class DevicePairingError(AppleDeviceError):
    """Raised when device pairing fails."""
    pass


class EnrollmentError(AppleDeviceError):
    """Raised when enrollment operations fail."""
    pass


class ActivationError(AppleDeviceError):
    """Raised when device activation fails."""
    pass


class RestoreError(AppleDeviceError):
    """Raised when device restore or erase operations fail."""
    pass


class OrganizationError(AppleDeviceError):
    """Raised when organization operations fail."""
    pass


class ToolNotFoundError(AppleDeviceError):
    """Raised when a required external tool is not found."""
    pass
