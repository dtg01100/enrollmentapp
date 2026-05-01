"""Tests for enrollment flows module."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class _NoDeviceConnectedError(Exception):
    """Simulates pymobiledevice3.lockdown.NoDeviceConnectedError for testing."""


class CloudConfigurationAlreadyPresentError(Exception):
    """Mock exception for testing."""


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


class TestFlowRegistry:
    """Test enrollment flow registry."""

    def test_flow_registry_list_flows(self, mock_pymobiledevice3):
        """Test: Flow registry lists available flows."""
        from apple_device_cli.enrollment.flows import FlowRegistry

        flows = FlowRegistry.list()
        flow_names = [f.name for f in flows]
        assert "simple-supervised" in flow_names
        assert "reenrollment" in flow_names

    def test_flow_registry_get_flow(self, mock_pymobiledevice3):
        """Test: Flow registry retrieves flow by name."""
        from apple_device_cli.enrollment.flows import FlowRegistry

        flow = FlowRegistry.get("simple-supervised")
        assert flow is not None
        assert flow.name == "simple-supervised"

    def test_flow_registry_register_custom_flow(self, mock_pymobiledevice3):
        """Test: Flow registry allows registering custom flows."""
        from apple_device_cli.enrollment.flows import FlowRegistry, EnrollmentFlow

        class CustomFlow(EnrollmentFlow):
            def __init__(self):
                super().__init__(name="custom", description="Custom flow")

            def execute(self, **kwargs):
                pass

        custom = CustomFlow()
        FlowRegistry.register(custom)

        retrieved = FlowRegistry.get("custom")
        assert retrieved is not None
        assert retrieved.name == "custom"


class TestSimpleSupervisedFlow:
    """Test simple supervised enrollment flow."""

    def test_simple_supervised_flow_attributes(self, mock_pymobiledevice3):
        """Test: SimpleSupervisedEnrollment has correct attributes."""
        from apple_device_cli.enrollment.flows import SimpleSupervisedEnrollment

        flow = SimpleSupervisedEnrollment()
        assert flow.name == "simple-supervised"
        assert "supervision" in flow.description.lower()


class TestReenrollmentFlow:
    """Test reenrollment flow."""

    def test_reenrollment_flow_attributes(self, mock_pymobiledevice3):
        """Test: ReenrollmentFlow has correct attributes."""
        from apple_device_cli.enrollment.flows import ReenrollmentFlow

        flow = ReenrollmentFlow()
        assert flow.name == "reenrollment"
        assert "cloud config" in flow.description.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
