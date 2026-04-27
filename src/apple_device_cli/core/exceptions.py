class AppleDeviceError(Exception): pass


class DeviceNotFoundError(AppleDeviceError): pass


class DevicePairingError(AppleDeviceError): pass


class EnrollmentError(AppleDeviceError): pass


class ActivationError(AppleDeviceError): pass


class RestoreError(AppleDeviceError): pass


class OrganizationError(AppleDeviceError): pass


class ToolNotFoundError(AppleDeviceError): pass