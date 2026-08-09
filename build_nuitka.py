#!/usr/bin/env python3
"""Build script for ios-enroll using Nuitka.

Nuitka compiles Python to C and then to a native standalone executable.
Windows targets use MSVC (``--msvc=latest``) on native Windows hosts and
MinGW (``--mingw64``) when cross-compiling from Linux/macOS on a Python
3.12-or-older host -- see ``_windows_compiler_args``.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / ".nuitka-build"


def clean() -> None:
    """Remove previous build artifacts."""
    for path in (DIST_DIR, BUILD_DIR, Path("build")):
        if path.exists():
            print(f"Removing {path}...")
            shutil.rmtree(path)
    for cache_dir in Path(".").glob("*.build"):
        print(f"Removing {cache_dir}...")
        shutil.rmtree(cache_dir)
    for onefile in Path(".").glob("*.onefile-build"):
        print(f"Removing {onefile}...")
        shutil.rmtree(onefile)


def base_args() -> list[str]:
    """Return base Nuitka arguments used by all targets."""
    return [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--output-dir=dist",
        "--include-package=apple_device_cli",
        "--include-package=pymobiledevice3",
        "--include-package=cryptography",
        "--include-package=typer",
        "--include-package=rich",
        "--enable-plugin=anti-bloat",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=pytest",
    ]


def run_nuitka(args: list[str]) -> int:
    """Run Nuitka and stream output."""
    print(f"Running: {' '.join(args)}")
    result = subprocess.run(args, check=False)
    return result.returncode


def require_pyside6() -> None:
    """Guard GUI targets with a friendly install hint.

    Mirrors the friendly message that ``apple_device_cli.gui_qt`` shows when
    PySide6 is missing at runtime, so the build script and the produced
    binary both fail in the same user-readable way.
    """
    try:
        import PySide6  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            "build_nuitka.py: PySide6 is not available. Install with: "
            "uv pip install 'ios-enroll[gui]'\n"
        )
        raise SystemExit(1) from exc


def build_cli() -> int:
    """Build Linux CLI onefile executable."""
    print("Building Linux CLI executable...")
    args = base_args()
    args.extend([
        "--onefile",
        "--output-filename=ios-enroll",
        str(ROOT / "src" / "apple_device_cli" / "cli.py"),
    ])
    return run_nuitka(args)


def build_gui() -> int:
    """Build Linux GUI executable."""
    print("Building Linux GUI executable...")
    require_pyside6()
    args = base_args()
    args.extend([
        "--onefile",
        "--output-filename=ios-enroll-gui",
        "--include-package=PySide6",
        "--enable-plugin=pyside6",
        str(ROOT / "src" / "apple_device_cli" / "gui_qt.py"),
    ])
    return run_nuitka(args)


def _windows_compiler_args() -> list[str]:
    """Return the Nuitka compiler flags for Windows targets.

    Native Windows builds (``sys.platform == "win32"``) use MSVC via
    ``--msvc=latest``: Nuitka rejects ``--mingw64`` on Python 3.13 or
    higher, and CPython 3.13+ for Windows is built with MSVC.

    Cross-compiles from Linux/macOS use MinGW (``--mingw64``), which
    Nuitka only supports on hosts older than Python 3.13. On 3.13+ hosts
    Nuitka aborts with a FATAL, so fail fast here with a fix hint instead.
    """
    if sys.platform == "win32":
        return ["--msvc=latest"]
    if sys.version_info >= (3, 13):
        sys.stderr.write(
            "build_nuitka.py: cannot cross-compile Windows targets on "
            "Python 3.13 or higher (Nuitka drops --mingw64 support there).\n"
            "  Build on a native Windows host with Python 3.13+ (uses MSVC), "
            "or use a Python 3.12-or-older host for MinGW cross-compiles.\n"
        )
        raise SystemExit(1)
    return ["--mingw64"]


def build_windows_cli() -> int:
    """Build Windows CLI executable (MSVC on Windows, MinGW elsewhere)."""
    print("Building Windows CLI executable...")
    args = base_args()
    args.extend([
        "--onefile",
        "--output-filename=ios-enroll.exe",
        *_windows_compiler_args(),
        str(ROOT / "src" / "apple_device_cli" / "cli.py"),
    ])
    icon = ROOT / "assets" / "ios-enroll.ico"
    if icon.exists():
        args.append(f"--windows-icon-from-ico={icon}")
    return run_nuitka(args)


def build_windows_gui() -> int:
    """Build Windows GUI executable (MSVC on Windows, MinGW elsewhere)."""
    print("Building Windows GUI executable...")
    require_pyside6()
    args = base_args()
    args.extend([
        "--onefile",
        "--output-filename=ios-enroll-gui.exe",
        "--include-package=PySide6",
        "--enable-plugin=pyside6",
        "--windows-console-mode=disable",
        *_windows_compiler_args(),
        str(ROOT / "src" / "apple_device_cli" / "gui_qt.py"),
    ])
    icon = ROOT / "assets" / "ios-enroll.ico"
    if icon.exists():
        args.append(f"--windows-icon-from-ico={icon}")
    return run_nuitka(args)


def main() -> int:
    args = sys.argv[1:]
    if "--clean" in args:
        args.remove("--clean")
        clean()

    target = args[0] if args else "all"

    valid_targets = {"all", "cli", "gui", "windows", "windows-cli", "windows-gui"}
    if target not in valid_targets:
        sys.stderr.write(
            f"build_nuitka.py: unknown target {target!r}. "
            f"Valid targets: {', '.join(sorted(valid_targets))}\n"
        )
        return 2

    status = 0

    # ``all`` means "all builds for the current platform" — Linux CLI + GUI
    # only. It deliberately does NOT include the MinGW Windows cross-compile
    # builders (those need an explicit ``windows*`` target and a MinGW-capable
    # host). This is what CI workflows and local dev use.
    if target in ("all", "cli"):
        status = build_cli() or status
    if target in ("all", "gui"):
        status = build_gui() or status
    # ``windows*`` targets always invoke the MinGW Windows builders, even
    # under ``all`` — explicit opt-in to cross-compile.
    if target in ("windows", "windows-cli"):
        status = build_windows_cli() or status
    if target in ("windows", "windows-gui"):
        status = build_windows_gui() or status

    if status:
        print("\nBuild failed.")
        return status

    print("\nBuild complete. Artifacts:")
    for path in sorted(DIST_DIR.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
