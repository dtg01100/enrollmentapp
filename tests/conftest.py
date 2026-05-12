"""Test configuration and shared fixtures."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock


def pytest_configure(config):
    """Mock optional native dependencies at the Python level if not installed.

    This allows tests to import the CLI modules even when:
    - pymobiledevice3 is not available (e.g., in CI without native compilation)
    - ipsw_parser is not available

    The production code should have direct imports (hard dependency).
    """
    # Mock pymobiledevice3 if not installed
    if "pymobiledevice3" not in sys.modules:
        # Create mock pymobiledevice3 module structure
        mock_pm3 = MagicMock()
        mock_pm3_lockdown = MagicMock()
        mock_pm3_ca = MagicMock()
        mock_pm3_services = MagicMock()
        mock_pm3_irecv = MagicMock()
        mock_pm3_restore = MagicMock()

        # Set up the mock hierarchy
        mock_pm3.lockdown = mock_pm3_lockdown
        mock_pm3.ca = mock_pm3_ca
        mock_pm3.services = mock_pm3_services
        mock_pm3.irecv = mock_pm3_irecv
        mock_pm3.restore = mock_pm3_restore

        # Mock IRecv with required attributes
        class MockIRecv:
            def __init__(self, **kwargs):
                self._device = None
        mock_pm3_irecv.IRecv = MockIRecv

        # Mock Behavior enum
        class MockBehavior:
            class Update:
                name = "Update"
            class Erase:
                name = "Erase"
        mock_pm3_restore.Behavior = MockBehavior

        # Inject into sys.modules
        sys.modules["pymobiledevice3"] = mock_pm3
        sys.modules["pymobiledevice3.lockdown"] = mock_pm3_lockdown
        sys.modules["pymobiledevice3.ca"] = mock_pm3_ca
        sys.modules["pymobiledevice3.services"] = mock_pm3_services
        sys.modules["pymobiledevice3.irecv"] = mock_pm3_irecv
        sys.modules["pymobiledevice3.restore"] = mock_pm3_restore
        sys.modules["pymobiledevice3.exceptions"] = MagicMock()
        sys.modules["pymobiledevice3.services.mobile_activation"] = MagicMock()
        sys.modules["pymobiledevice3.services.mobile_config"] = MagicMock()
        sys.modules["pymobiledevice3.restore.device"] = mock_pm3_restore
        sys.modules["pymobiledevice3.restore.restore"] = mock_pm3_restore
        sys.modules["pymobiledevice3.restore.base_restore"] = mock_pm3_restore

    # Mock ipsw_parser if not installed
    if "ipsw_parser" not in sys.modules:
        mock_ipsw = MagicMock()
        sys.modules["ipsw_parser"] = mock_ipsw
        sys.modules["ipsw_parser.ipsw"] = mock_ipsw
