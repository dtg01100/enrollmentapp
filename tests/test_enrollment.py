import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class _NoDeviceConnectedError(Exception):
    """Simulates pymobiledevice3.lockdown.NoDeviceConnectedError for testing."""
    pass


@pytest.fixture(autouse=True)
def mock_pymobiledevice3():
    mock_pm3 = MagicMock()
    mock_pm3.lockdown.create_using_usbmux = AsyncMock()
    mock_pm3.lockdown.LockdownClient = MagicMock()
    mock_pm3.lockdown.NoDeviceConnectedError = _NoDeviceConnectedError

    mock_mobile_config = MagicMock()
    mock_mobile_config.MobileConfigService = MagicMock()
    mock_mobile_config.CloudConfigurationAlreadyPresentError = CloudConfigurationAlreadyPresentError
    mock_pm3.services.mobile_config = mock_mobile_config

    mock_mobile_activation = MagicMock()
    mock_mobile_activation.MobileActivationService = MagicMock()
    mock_pm3.services.mobile_activation = mock_mobile_activation

    mock_pm3.ca.create_keybag_file = MagicMock()

    with patch.dict('sys.modules', {
        'pymobiledevice3': mock_pm3,
        'pymobiledevice3.lockdown': mock_pm3.lockdown,
        'pymobiledevice3.services': MagicMock(),
        'pymobiledevice3.services.mobile_config': mock_pm3.services.mobile_config,
        'pymobiledevice3.services.mobile_activation': mock_pm3.services.mobile_activation,
        'pymobiledevice3.ca': mock_pm3.ca,
    }):
        yield mock_pm3


class CloudConfigurationAlreadyPresentError(Exception):
    """Mock exception for testing."""
    pass


class TestSupervisedPairing:
    def test_module_imports(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import supervised
        assert hasattr(supervised, "do_supervised_pairing")
        assert hasattr(supervised, "make_supervised")

    def test_make_supervised_with_invalid_paths(self, mock_pymobiledevice3):
        mock_pymobiledevice3.lockdown.create_using_usbmux = AsyncMock(
            side_effect=_NoDeviceConnectedError("No device")
        )
        from apple_device_cli.enrollment import supervised
        from apple_device_cli.core.exceptions import EnrollmentError
        with pytest.raises(EnrollmentError):
            supervised.make_supervised(
                "/nonexistent/cert.der",
                "/nonexistent/key.der",
                "Test Org",
                None,
                ["Location", "ApplePay"],
            )


class TestActivation:
    def test_module_imports(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import activation
        assert hasattr(activation, "do_activate")
        assert hasattr(activation, "activate_device")

    def test_activate_device_without_hardware(self, mock_pymobiledevice3):
        from apple_device_cli.enrollment import activation
        from apple_device_cli.core.exceptions import ActivationError
        with patch.object(activation, 'create_using_usbmux', AsyncMock(
                side_effect=_NoDeviceConnectedError("No device")
            )):
            with pytest.raises(ActivationError):
                activation.activate_device()