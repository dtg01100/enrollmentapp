"""Test configuration and shared fixtures."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Capture the real pymobiledevice3 modules at conftest load time, *before*
# pytest_configure() runs. If pymobiledevice3 is not importable, fall back to
# the bare-mock pytest_configure shim — the fixture will then also degrade to
# bare mocks and emit a warning when used.
try:
    import pymobiledevice3
    from pymobiledevice3 import ca, lockdown, services
    from pymobiledevice3.services import mobile_activation, mobile_config

    _REAL_PYMOBILEDEVICE3_MODULES = {
        "pymobiledevice3": pymobiledevice3,
        "pymobiledevice3.ca": ca,
        "pymobiledevice3.lockdown": lockdown,
        "pymobiledevice3.services": services,
        "pymobiledevice3.services.mobile_config": mobile_config,
        "pymobiledevice3.services.mobile_activation": mobile_activation,
    }
    _PYMOBILEDEVICE3_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on bare CI runners
    _REAL_PYMOBILEDEVICE3_MODULES = {}
    _PYMOBILEDEVICE3_AVAILABLE = False


# --- Mock exception classes for tests that simulate pymobiledevice3 errors ---


class MockNoDeviceConnectedError(Exception):
    """Simulates pymobiledevice3.lockdown.NoDeviceConnectedError for testing."""


class MockCloudConfigurationAlreadyPresentError(Exception):
    """Simulates pymobiledevice3.services.mobile_config.CloudConfigurationAlreadyPresentError for testing."""


def pytest_configure(config):
    """Mock optional native dependencies at the Python level if not installed.

    This allows tests to import the CLI modules even when pymobiledevice3 is
    not available (e.g. on a bare CI runner without native compilation). When
    pymobiledevice3 IS available, pytest_configure leaves sys.modules alone —
    the per-test ``mock_pymobiledevice3`` fixture handles mocking with a
    spec'd version of the real modules.
    """
    # Mock pymobiledevice3 only if it was not importable at conftest load.
    if "pymobiledevice3" not in sys.modules:
        mock_pm3 = MagicMock()
        mock_pm3_lockdown = MagicMock()
        mock_pm3_ca = MagicMock()
        mock_pm3_services = MagicMock()

        mock_pm3.lockdown = mock_pm3_lockdown
        mock_pm3.ca = mock_pm3_ca
        mock_pm3.services = mock_pm3_services

        sys.modules["pymobiledevice3"] = mock_pm3
        sys.modules["pymobiledevice3.lockdown"] = mock_pm3_lockdown
        sys.modules["pymobiledevice3.ca"] = mock_pm3_ca
        sys.modules["pymobiledevice3.services"] = mock_pm3_services
        sys.modules["pymobiledevice3.exceptions"] = MagicMock()
        sys.modules["pymobiledevice3.services.mobile_activation"] = MagicMock()
        sys.modules["pymobiledevice3.services.mobile_config"] = MagicMock()

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

    If pymobiledevice3 is not importable in this environment, the fixture
    falls back to bare mocks (with a warning) so tests can still run.
    """
    if not _PYMOBILEDEVICE3_AVAILABLE:  # pragma: no cover
        import warnings

        warnings.warn(
            "pymobiledevice3 is not importable; mock_pymobiledevice3 fixture "
            "is using bare MagicMock fallbacks. Attribute access will NOT be "
            "type-checked against the real pymobiledevice3 API.",
            stacklevel=2,
        )
        # Minimal bare-mock fallback for environments without pymobiledevice3.
        mock_pm3 = MagicMock()
        mock_pm3.lockdown.create_using_usbmux = AsyncMock()
        mock_pm3.lockdown.NoDeviceConnectedError = MockNoDeviceConnectedError
        mock_pm3.services.mobile_config.CloudConfigurationAlreadyPresentError = (
            MockCloudConfigurationAlreadyPresentError
        )
        mock_pm3.ca.create_keybag_file = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "pymobiledevice3": mock_pm3,
                "pymobiledevice3.lockdown": mock_pm3.lockdown,
                "pymobiledevice3.services": MagicMock(),
                "pymobiledevice3.services.mobile_config": mock_pm3.services.mobile_config,
                "pymobiledevice3.services.mobile_activation": mock_pm3.services.mobile_activation,
                "pymobiledevice3.ca": mock_pm3.ca,
            },
        ):
            yield mock_pm3
        return

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
