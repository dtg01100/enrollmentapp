#!/usr/bin/env python3
"""Build script for ios-enroll using Nuitka.

Nuitka compiles Python to C and then to a native standalone executable.
Windows targets use MSVC (``--msvc=latest``) on native Windows hosts and
MinGW (``--mingw64``) when cross-compiling from Linux/macOS on a Python
3.12-or-older host -- see ``_windows_compiler_args``.

Build modes:

* Default (development): ``--standalone``, ``--lto=no``, ``--python-flag=-O``,
  parallel jobs, shared cache. The fastest setup for iterating on code; the
  produced binary is a directory distribution, not a single ``.exe``.
* ``--release``: re-enables ``--onefile`` and link-time optimization for
  shippable artifacts. Slower to build, single self-extracting executable.

Compiler selection:

* Native Windows: prefers ClangCL when available (``clang-cl`` on PATH or
  ``NUITKA_USE_CLANG=1``), otherwise MSVC. Set ``NUITKA_USE_CLANG=0`` to
  force MSVC.
* MinGW cross-compile from Linux/macOS: links with LLD when available for
  noticeably faster link times.
"""
from __future__ import annotations

import concurrent.futures
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

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


def _jobs_arg() -> str:
    """Return ``--jobs=N`` using ``NUITKA_JOBS`` or detected CPU count."""
    n = os.environ.get("NUITKA_JOBS") or str(os.cpu_count() or 1)
    return f"--jobs={n}"


def _cache_dir_arg() -> str:
    """Return ``--cache-dir=<path>`` shared across all targets in a tree.

    ``NUITKA_CACHE_DIR`` overrides the default. CI runners should set this
    to a path that's cached across runs (see ``.github/workflows/build.yml``).
    """
    cache_dir = os.environ.get("NUITKA_CACHE_DIR") or str(ROOT / ".nuitka-cache")
    return f"--cache-dir={cache_dir}"


def base_args(release: bool = False) -> list[str]:
    """Return base Nuitka arguments used by all targets.

    ``release=False`` (default) trades a slightly larger/faster binary for
    a much shorter build: skip ``--onefile``, skip LTO, drop docstrings.
    Pass ``release=True`` (via the ``--release`` CLI flag) to re-enable
    those for shipping artifacts.
    """
    args = [
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
        _jobs_arg(),
        _cache_dir_arg(),
    ]
    if release:
        args.append("--onefile")
    else:
        # Development build: cheaper compile, smaller intermediate C.
        args.extend([
            "--python-flag=-O",
            "--no-docstrings",
            "--lto=no",
        ])
    # Drop unused pymobiledevice3/rich submodules that drag in extra
    # packages (tornado is only needed by pymobiledevice3's developer
    # services, never by supervised enrollment).
    args.extend([
        "--nofollow-import-to=tornado",
        "--nofollow-import-to=ipython",
        "--nofollow-import-to=jupyter",
        "--nofollow-import-to=notebook",
        "--nofollow-import-to=rich.jupyter",
    ])
    return args


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


def build_cli(release: bool = False) -> int:
    """Build CLI executable (Linux/macOS host)."""
    print(f"Building CLI executable (release={release})...")
    args = base_args(release=release)
    args.extend([
        "--output-filename=ios-enroll",
        str(ROOT / "src" / "apple_device_cli" / "cli.py"),
    ])
    return run_nuitka(args)


def build_gui(release: bool = False) -> int:
    """Build GUI executable (Linux/macOS host)."""
    print(f"Building GUI executable (release={release})...")
    require_pyside6()
    args = base_args(release=release)
    args.extend([
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

    If ClangCL (``clang-cl``) is on PATH, or ``NUITKA_USE_CLANG=1`` is
    set, pass ``--clang`` too: clang's optimizer beats MSVC's cl.exe on
    the small-to-medium C files Nuitka emits, typically 10-25% faster on
    the compile step. ``NUITKA_USE_CLANG=0`` forces MSVC.

    Cross-compiles from Linux/macOS use MinGW (``--mingw64``), which
    Nuitka only supports on hosts older than Python 3.13. On 3.13+ hosts
    Nuitka aborts with a FATAL, so fail fast here with a fix hint instead.
    When ``lld`` is available on the MinGW cross-compile host, append
    ``-fuse-ld=lld`` to use LLVM's linker (much faster than MinGW's
    default ``ld`` for large binaries).
    """
    if sys.platform == "win32":
        force_clang = os.environ.get("NUITKA_USE_CLANG")
        if force_clang == "0":
            return ["--msvc=latest"]
        if force_clang == "1" or shutil.which("clang-cl"):
            return ["--clang", "--msvc=latest"]
        return ["--msvc=latest"]
    if sys.version_info >= (3, 13):
        sys.stderr.write(
            "build_nuitka.py: cannot cross-compile Windows targets on "
            "Python 3.13 or higher (Nuitka drops --mingw64 support there).\n"
            "  Build on a native Windows host with Python 3.13+ (uses MSVC), "
            "or use a Python 3.12-or-older host for MinGW cross-compiles.\n"
        )
        raise SystemExit(1)
    flags = ["--mingw64"]
    if shutil.which("ld.lld") or shutil.which("lld-link"):
        flags.append("-fuse-ld=lld")
    return flags


def build_windows_cli(release: bool = False) -> int:
    """Build Windows CLI executable (MSVC on Windows, MinGW elsewhere)."""
    print(f"Building Windows CLI executable (release={release})...")
    args = base_args(release=release)
    args.extend([
        "--output-filename=ios-enroll.exe",
        *_windows_compiler_args(),
        str(ROOT / "src" / "apple_device_cli" / "cli.py"),
    ])
    icon = ROOT / "assets" / "ios-enroll.ico"
    if icon.exists():
        args.append(f"--windows-icon-from-ico={icon}")
    return run_nuitka(args)


def build_windows_gui(release: bool = False) -> int:
    """Build Windows GUI executable (MSVC on Windows, MinGW elsewhere)."""
    print(f"Building Windows GUI executable (release={release})...")
    require_pyside6()
    args = base_args(release=release)
    args.extend([
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


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested build target(s).

    ``argv`` defaults to ``sys.argv[1:]`` when called as a script. Accepting
    it as a parameter makes the function directly testable without
    monkeypatching ``sys.argv``.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    release = "--release" in args
    if release:
        args.remove("--release")

    parallel = "--parallel" in args
    if parallel:
        args.remove("--parallel")

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

    # ``all`` means "all builds for the current platform" — Linux/macOS CLI +
    # GUI only. It deliberately does NOT include the MinGW Windows cross-
    # compile builders (those need an explicit ``windows*`` target and a
    # MinGW-capable host). This is what CI workflows and local dev use.
    builders: list[tuple[str, Callable[[], int]]] = []
    if target in ("all", "cli"):
        builders.append(("cli", lambda: build_cli(release=release)))
    if target in ("all", "gui"):
        builders.append(("gui", lambda: build_gui(release=release)))
    # ``windows*`` targets always invoke the Windows builders, even under
    # ``all`` — explicit opt-in to cross-compile.
    if target in ("windows", "windows-cli"):
        builders.append(("windows-cli", lambda: build_windows_cli(release=release)))
    if target in ("windows", "windows-gui"):
        builders.append(("windows-gui", lambda: build_windows_gui(release=release)))

    status = 0
    if parallel and len(builders) > 1:
        # Run independent builds concurrently — biggest wins overlap the
        # heavy C-compile phase of pymobiledevice3 + cryptography between
        # CLI and GUI targets. ``run_nuitka`` already streams output, so
        # interleaved prints from two threads are acceptable.
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(builders)) as ex:
            futures = {ex.submit(fn): name for name, fn in builders}
            for future in concurrent.futures.as_completed(futures):
                status = future.result() or status
    else:
        for _name, fn in builders:
            status = fn() or status

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
