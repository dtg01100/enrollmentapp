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


__all__ = [
    "MainWindow",
    "EnrollmentApp",  # back-compat alias for MainWindow
    "OrgValidationError",
    "WorkerThread",
    "run_gui",
    "validate_identity_days",
    "validate_org_fields",
]
