"""Test configuration and shared fixtures."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# pymobiledevice3 is a hard runtime dependency — the production CLI imports
# it at module top-level (apple_device_cli/device/connection.py and
# apple_device_cli/enrollment/supervised.py), and the core enrollment
# workflow cannot run without it. The mock layers below (the
# ``mock_pymobiledevice3`` fixture, etc.) use spec'd MagicMocks against the
# real class shapes for unit testing, so the real package MUST be
# importable on every test machine.
#
# Fail fast at conftest load time with an actionable install message
# rather than letting tests collect and then die with confusing
# ImportErrors halfway through.
try:
    import pymobiledevice3
    from pymobiledevice3 import ca, lockdown, services
    from pymobiledevice3.services import mobile_activation, mobile_config
    from pymobiledevice3.lockdown import LockdownClient
    from pymobiledevice3.services.mobile_activation import MobileActivationService
    from pymobiledevice3.services.mobile_config import MobileConfigService
except ImportError as exc:  # pragma: no cover - exercised only when dep is genuinely missing
    raise ImportError(
        "pymobiledevice3 is a required test dependency (it's also a hard runtime "
        "dependency of ios-enroll — device connection and supervised enrollment "
        "cannot work without it). Install it with:\n"
        "    uv pip install pymobiledevice3\n"
        "or install the project with all runtime deps:\n"
        "    uv pip install -e ."
    ) from exc


_REAL_PYMOBILEDEVICE3_MODULES = {
    "pymobiledevice3": pymobiledevice3,
    "pymobiledevice3.ca": ca,
    "pymobiledevice3.lockdown": lockdown,
    "pymobiledevice3.services": services,
    "pymobiledevice3.services.mobile_config": mobile_config,
    "pymobiledevice3.services.mobile_activation": mobile_activation,
}


# Re-exported for test modules that need to spec their mocks against the real
# pymobiledevice3 classes. The autouse ``mock_pymobiledevice3`` fixture patches
# pymobiledevice3 in sys.modules with spec'd MagicMocks, so a test doing
# ``from pymobiledevice3.lockdown import LockdownClient`` would resolve to a
# Mock — which makes ``MagicMock(spec=LockdownClient)`` raise
# ``InvalidSpecError``. Test files must import these from conftest instead.
__all__ = [
    "LockdownClient",
    "MobileActivationService",
    "MobileConfigService",
    "MockNoDeviceConnectedError",
    "MockCloudConfigurationAlreadyPresentError",
]


# --- Mock exception classes for tests that simulate pymobiledevice3 errors ---


class MockNoDeviceConnectedError(Exception):
    """Simulates pymobiledevice3.lockdown.NoDeviceConnectedError for testing."""


class MockCloudConfigurationAlreadyPresentError(Exception):
    """Simulates pymobiledevice3.services.mobile_config.CloudConfigurationAlreadyPresentError for testing."""


def pytest_configure(config):
    """Mock optional native dependencies that production code imports lazily.

    Production code imports ``ipsw_parser`` at module top-level (used for
    IPSW parsing utilities) but the import is wrapped in try/except in the
    relevant module. This shim lets those modules import cleanly on a
    bare machine without the optional native dep.

    pymobiledevice3 is NOT mocked here — it's a hard requirement enforced
    at the top of this file.
    """
    if "ipsw_parser" not in sys.modules:
        mock_ipsw = MagicMock()
        sys.modules["ipsw_parser"] = mock_ipsw
        sys.modules["ipsw_parser.ipsw"] = mock_ipsw


# --- Shared pymobiledevice3 mock fixture ---


@pytest.fixture(autouse=True)
def mock_pymobiledevice3():
    """Patch a spec'd pymobiledevice3 into sys.modules for each test.

    Unlike a bare MagicMock(), this fixture uses ``spec=`` against the real
    pymobiledevice3 modules so that:

    * Accessing an attribute that doesn't exist on the real module raises
      ``AttributeError`` instead of silently returning another mock — catching
      typos and dead code paths in production.
    * Default async methods (e.g. ``lockdown.create_using_usbmux``,
      ``ca.create_keybag_file``) are spec'd against the real coroutine /
      function, so calling them with the wrong arguments raises ``TypeError``.

    Tests that need to customize behavior reassign these attributes on the
    yielded mock (e.g. ``mock.lockdown.create_using_usbmux = AsyncMock(...)``);
    the underlying attribute is real, so the reassignment is allowed.

    This fixture is autouse=True so any test exercising code paths that touch
    pymobiledevice3 gets a fresh, fully-formed mock hierarchy. Tests that don't
    touch pymobiledevice3 pay only the cost of a no-op patch.dict.
    """
    ca = _REAL_PYMOBILEDEVICE3_MODULES["pymobiledevice3.ca"]
    lockdown = _REAL_PYMOBILEDEVICE3_MODULES["pymobiledevice3.lockdown"]
    services = _REAL_PYMOBILEDEVICE3_MODULES["pymobiledevice3.services"]
    mobile_config = _REAL_PYMOBILEDEVICE3_MODULES["pymobiledevice3.services.mobile_config"]
    mobile_activation = _REAL_PYMOBILEDEVICE3_MODULES["pymobiledevice3.services.mobile_activation"]
    pymobiledevice3 = _REAL_PYMOBILEDEVICE3_MODULES["pymobiledevice3"]

    # Build a spec'd module hierarchy — accessing unknown attrs raises AttributeError.
    mock_pm3 = MagicMock(spec=pymobiledevice3)
    mock_pm3.ca = MagicMock(spec=ca)
    mock_pm3.lockdown = MagicMock(spec=lockdown)
    mock_pm3.services = MagicMock(spec=services)
    mock_pm3.services.mobile_config = MagicMock(spec=mobile_config)
    mock_pm3.services.mobile_activation = MagicMock(spec=mobile_activation)

    # Exception class assignments (real attributes of the spec'd modules).
    mock_pm3.lockdown.NoDeviceConnectedError = MockNoDeviceConnectedError
    mock_pm3.services.mobile_config.CloudConfigurationAlreadyPresentError = (
        MockCloudConfigurationAlreadyPresentError
    )

    # Default async/sync mocks for the entry points production code calls, spec'd
    # against the real callables so wrong-arg calls raise TypeError loudly.
    mock_pm3.lockdown.create_using_usbmux = AsyncMock(spec=lockdown.create_using_usbmux)
    mock_pm3.ca.create_keybag_file = MagicMock(spec=ca.create_keybag_file)

    with patch.dict(
        "sys.modules",
        {
            "pymobiledevice3": mock_pm3,
            "pymobiledevice3.lockdown": mock_pm3.lockdown,
            "pymobiledevice3.services": mock_pm3.services,
            "pymobiledevice3.services.mobile_config": mock_pm3.services.mobile_config,
            "pymobiledevice3.services.mobile_activation": mock_pm3.services.mobile_activation,
            "pymobiledevice3.ca": mock_pm3.ca,
        },
    ):
        yield mock_pm3
