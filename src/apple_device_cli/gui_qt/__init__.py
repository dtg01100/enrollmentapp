"""PySide6 GUI for ios-enroll.

Public API (back-compat with the pre-refactor single-file layout).

Lazy symbols (materialized by _require_pyside6 on first access):
    MainWindow, EnrollmentApp (back-compat alias), WorkerThread

Eagerly re-exported from app.py:
    Everything app.py imports + its own module-level definitions.
    Tests use patch() on gui_qt.<name> and expect <name> to be resolvable
    as an attribute of this package -- so 'from .app import *' mirrors
    app.py full public surface.
"""
import sys as _sys
import types as _types

from apple_device_cli.gui_qt.app import *  # noqa: F401, F403

# Underscore-prefixed names are NOT covered by import *; re-export explicitly.
from apple_device_cli.gui_qt.app import (  # noqa: F401
    OrgValidationError,
    _cert_expiry,
    _device_ecid,
    _filename_from_url,
    _format_cert_expiry_badge,
    _main,
    _redact_in_text,
    _require_pyside6,
    _write_identity_atomic,
    run_gui,
    validate_identity_days,
    validate_org_fields,
)


_QT_SYMBOLS = frozenset(
    {
        "QApplication",
        "QCheckBox",
        "QCloseEvent",
        "QComboBox",
        "QDialog",
        "QDialogButtonBox",
        "QEvent",
        "QFileDialog",
        "QFormLayout",
        "QFrame",
        "QHBoxLayout",
        "QHeaderView",
        "QInputDialog",
        "QKeySequence",
        "QLabel",
        "QLineEdit",
        "QListWidget",
        "QListWidgetItem",
        "QMainWindow",
        "QMenu",
        "QMessageBox",
        "QPlainTextEdit",
        "QProgressBar",
        "QPushButton",
        "QSettings",
        "QShortcut",
        "QSizePolicy",
        "QSpacerItem",
        "QSplitter",
        "QStandardPaths",
        "QStatusBar",
        "QStyle",
        "QTabWidget",
        "QTableWidget",
        "QTableWidgetItem",
        "QTextEdit",
        "QThread",
        "QTimer",
        "QToolBar",
        "QVBoxLayout",
        "QWidget",
        "Qt",
        "Signal",
        "Slot",
    }
)


def __getattr__(name: str):  # PEP 562
    """Lazy attribute access for Qt-using symbols materialized by _require_pyside6().

    Called by Python when an attribute lookup falls through. For known Qt
    symbols and for the top-level MainWindow/EnrollmentApp/WorkerThread
    names, triggers _require_pyside6() on first access. Lets
    ``monkeypatch.setattr`` and normal imports resolve symbols that only
    exist after the Qt runtime loads.
    """
    if name in ("MainWindow", "EnrollmentApp", "WorkerThread") or name in _QT_SYMBOLS:
        from apple_device_cli.gui_qt import app as _app
        if not hasattr(_app, name):
            _require_pyside6()
        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Promote the package's class so PEP 562's ``__getattr__`` (above) and the
# ``__setattr__`` defined here are actually invoked. Python looks up module
# ``__getattr__`` / ``__setattr__`` on the module's *type*, not on the
# instance dict, so without this swap assigning to ``__setattr__`` on this
# module's globals has no effect — the default ``ModuleType.__setattr__``
# (which writes directly to ``__dict__``) keeps running and test patches
# like ``monkeypatch.setattr(gui_qt, "list_signed_versions", lambda)`` only
# update the package namespace, never the ``gui_qt.app`` submodule where
# production code looks the name up.
#
# Subclassing ``ModuleType`` lets us:
#   * Define a real ``__setattr__`` that mirrors writes onto
#     ``gui_qt.app`` for names that already exist there (so test patches
#     affect production code after the gui_qt.py → gui_qt/ package split).
#   * Inherit the standard ``ModuleType.__getattr__`` behavior, which PEP
#     562 module-level ``__getattr__`` augments — no compatibility cost.
# Rebinding ``__class__`` on an already-initialized package is supported by
# CPython (the new class must be a ModuleType subclass with the same name).
class _PackageModule(_types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        # Default: write to this package's own __dict__.
        super().__setattr__(name, value)
        # Mirror onto gui_qt.app for names that already exist there, so
        # ``monkeypatch.setattr(gui_qt, X, Y)`` updates both namespaces.
        try:
            from apple_device_cli.gui_qt import app as _app_module
        except ImportError:
            return
        if name in _app_module.__dict__:
            _app_module.__dict__[name] = value


# ``__class__`` swap is only legal at import time, while CPython's
# ``module.__init__`` is willing to accept a new class on the half-built
# module. Doing it here (immediately after defining the class) is safe.
#
# When this module is loaded by ``runpy.run_module(..., run_name=...)``,
# ``__name__`` is the synthetic runpy name (e.g. ``"__not_main__"``), not
# the canonical package name. Resolve the real package module via
# ``sys.modules`` so the class swap targets the right object either way.
_real_module_name = "apple_device_cli.gui_qt"
self_module = _sys.modules.get(_real_module_name)
if (
    self_module is not None
    and self_module.__class__ is _types.ModuleType
    and self_module is _sys.modules[__name__]
):
    self_module.__class__ = _PackageModule


__all__ = [  # noqa: F405 — PEP 562 lazy __getattr__ resolves these via _app module
    "MainWindow",
    "EnrollmentApp",  # back-compat alias for MainWindow
    "OrgValidationError",
    "WorkerThread",
    "run_gui",
    "validate_identity_days",
    "validate_org_fields",
]
