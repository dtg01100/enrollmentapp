#!/usr/bin/env python3
"""Build script for ios-enroll using Nuitka.

Nuitka compiles Python to C and then to a native standalone executable.
Windows executables can be produced on Linux by installing a MinGW cross
compiler and passing --mingw64 to Nuitka.
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


def build_windows_cli() -> int:
    """Build Windows CLI executable from Linux using MinGW."""
    print("Building Windows CLI executable...")
    args = base_args()
    args.extend([
        "--onefile",
        "--output-filename=ios-enroll.exe",
        "--mingw64",
        str(ROOT / "src" / "apple_device_cli" / "cli.py"),
    ])
    icon = ROOT / "assets" / "ios-enroll.ico"
    if icon.exists():
        args.append(f"--windows-icon-from-ico={icon}")
    return run_nuitka(args)


def build_windows_gui() -> int:
    """Build Windows GUI executable from Linux using MinGW."""
    print("Building Windows GUI executable...")
    require_pyside6()
    args = base_args()
    args.extend([
        "--onefile",
        "--output-filename=ios-enroll-gui.exe",
        "--include-package=PySide6",
        "--enable-plugin=pyside6",
        "--windows-console-mode=disable",
        "--mingw64",
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
