"""Test configuration and shared fixtures.

Includes per-tab GUI fixtures (``make_app``, ``sample_org``,
``sample_devices``) extracted from tests/test_gui_qt.py for the Round 3
test split (Step 12).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

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

    # Swap only the pymobiledevice3 entries in sys.modules, restoring them by
    # key on teardown. Do NOT use patch.dict("sys.modules", ...) here:
    # unittest.mock's _patch_dict restores the dict by clearing it entirely and
    # re-applying the snapshot taken at entry, so ANY module lazily imported
    # during the test (e.g. ``rich``, which typer imports the first time
    # ``--help`` renders) is silently removed from sys.modules. The stale module
    # objects survive via package attributes (typer.rich_utils), so the next
    # test that re-imports rich gets a second set of classes and isinstance
    # checks across the two copies fail with confusing TypeErrors.
    patched_modules = {
        "pymobiledevice3": mock_pm3,
        "pymobiledevice3.lockdown": mock_pm3.lockdown,
        "pymobiledevice3.services": mock_pm3.services,
        "pymobiledevice3.services.mobile_config": mock_pm3.services.mobile_config,
        "pymobiledevice3.services.mobile_activation": mock_pm3.services.mobile_activation,
        "pymobiledevice3.ca": mock_pm3.ca,
    }
    original_modules = {name: sys.modules[name] for name in patched_modules if name in sys.modules}
    sys.modules.update(patched_modules)
    try:
        yield mock_pm3
    finally:
        for name in patched_modules:
            if name in original_modules:
                sys.modules[name] = original_modules[name]
            else:
                sys.modules.pop(name, None)


# --- GUI test fixtures ---------------------------------------------------------
#
# Extracted from tests/test_gui_qt.py so per-tab test files
# (test_gui_devices_tab.py, test_gui_orgs_tab.py, etc.) can reuse the
# same ``make_app``, ``sample_org``, and ``sample_devices`` fixtures
# without duplicating the SyncWorker plumbing.
#
# Round 3 step 12: the test split. Each tab's tests live in their own
# file; this module is the single source of truth for GUI test fixtures.


# PySide6 is required for the GUI fixtures below. Imports live here (not
# at module top) so the pymobiledevice3-specific fixtures above stay
# importable on a headless CI run that only exercises CLI logic.
from PySide6.QtWidgets import QApplication  # noqa: E402

from apple_device_cli.device.info import DeviceInfo  # noqa: E402
from apple_device_cli.orgs.manager import Organization, OrganizationManager  # noqa: E402


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Single QApplication for the whole test session.

    The finalizer calls ``app.quit()`` and drains pending events so pytest
    can exit cleanly under the offscreen Qt platform plugin. Without it,
    ``pytest tests/test_gui_qt.py`` (running this file in isolation) hangs
    after the test summary line because QApplication keeps the interpreter
    alive on its pending-event queue.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    app.quit()
    app.processEvents()


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """Make QMessageBox.{warning,critical,information,question} return immediately.

    Modal dialogs block the test runner forever under the offscreen Qt
    platform plugin because no user input can ever dismiss them. Production
    code still calls the real dialogs; tests just short-circuit them. Tests
    that need to inspect ``question`` prompts monkeypatch ``question``
    themselves (the per-test monkeypatch wins over this autouse one).
    """
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    yield


@pytest.fixture
def sample_devices() -> list[DeviceInfo]:
    return [
        DeviceInfo(
            udid="00008101-001234567890ABCD",
            device_name="Test iPhone",
            device_type="iPhone14,2",
            firmware_version="17.0",
            build_version="21A329",
            ecid="0x1234",
        )
    ]


@pytest.fixture
def sample_org(tmp_path) -> Organization:
    return Organization(
        name="Capital Candy",
        org_id="com.capitalcandy",
        mdm_url="https://mdm.example.com/mdm",
        cert_path=str(tmp_path / "cert.der"),
        key_path=str(tmp_path / "key.der"),
    )


@pytest.fixture
def make_app(qapp, tmp_path, monkeypatch):
    """Factory for an EnrollmentApp wired to a tmp orgs dir.

    Replaces ``WorkerThread`` with a synchronous fake so handlers run on the
    calling thread. Without an event loop, real QThread pending connections
    would never fire their slots, so the test would deadlock in ``wait``.
    """
    from apple_device_cli.gui_qt import EnrollmentApp

    def _factory(orgs: list[Organization] | None = None) -> EnrollmentApp:
        from apple_device_cli import gui_qt

        class _Signal:
            def __init__(self):
                self._slots: list = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self, *args):
                for slot in list(self._slots):
                    slot(*args)

        class SyncWorker:
            def __init__(self, fn):
                self.fn = fn
                self.result = None
                self.error: Exception | None = None
                self.completed = _Signal()
                self.finished = _Signal()

            def start(self):
                try:
                    self.result = self.fn()
                except Exception as exc:  # noqa: BLE001
                    self.error = exc
                self.completed.emit(self.result, self.error)
                self.finished.emit()

            def quit(self):
                pass

            def wait(self, timeout=0):
                return True

        monkeypatch.setattr(gui_qt, "WorkerThread", SyncWorker)

        original_init = OrganizationManager.__init__

        def patched_init(self, orgs_dir=None):
            original_init(self, orgs_dir=tmp_path)

        with patch.object(OrganizationManager, "__init__", patched_init):
            with patch("apple_device_cli.gui_qt.list_devices", return_value=[]):
                with patch(
                    "apple_device_cli.gui_qt.OrganizationManager.list_orgs",
                    return_value=orgs or [],
                ):
                    return gui_qt.EnrollmentApp()

    return _factory