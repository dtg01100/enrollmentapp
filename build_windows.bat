@echo off
REM Build ios-enroll for Windows using Nuitka.
REM
REM This script ALWAYS produces Windows executables (.exe). It cannot
REM produce Linux binaries — use build.sh on a Linux host for those.
REM
REM Usage:
REM   build_windows.bat                  REM build both Windows CLI + GUI
REM   build_windows.bat windows          REM same as above (explicit)
REM   build_windows.bat windows-cli      REM Windows CLI only — no PySide6 install
REM   build_windows.bat windows-gui      REM Windows GUI only — requires PySide6
REM   build_windows.bat --clean [TARGET] REM remove build artifacts first
REM
REM Per-target extras are installed so a CLI-only build on a fresh
REM checkout doesn't pull PySide6.
setlocal enabledelayedexpansion

set TARGET=%1
if "%TARGET%"=="" set TARGET=windows

REM ``--clean [TARGET]`` — shift the real target to %2.
if /I "%TARGET%"=="--clean" (
    set TARGET=%2
    if "%TARGET%"=="" set TARGET=windows
)

REM Windows builds only accept Windows targets. Reject ambiguous names
REM like "all", "cli", "gui" (which would invoke the Linux builders in
REM build_nuitka.py and emit ELF binaries, not .exe files).
set VALID=0
if /I "%TARGET%"=="windows"      set VALID=1
if /I "%TARGET%"=="windows-cli"  set VALID=1
if /I "%TARGET%"=="windows-gui"  set VALID=1
if "%VALID%"=="0" (
    echo build_windows.bat: unknown target '%TARGET%'. >&2
    echo. >&2
    echo This script builds Windows executables only. Valid targets: >&2
    echo     windows       - both Windows CLI and GUI >&2
    echo     windows-cli   - Windows CLI only ^(.exe^, no PySide6^) >&2
    echo     windows-gui   - Windows GUI only ^(.exe, requires PySide6^) >&2
    echo. >&2
    echo For Linux builds, run build.sh on a Linux host. >&2
    exit /b 2
)

REM Decide whether the [gui] extra is needed. The [build] extra (Nuitka
REM + helpers) is always needed because build_nuitka.py runs Nuitka
REM regardless of the target.
set EXTRAS=build
if /I "%TARGET%"=="windows"      set EXTRAS=build,gui
if /I "%TARGET%"=="windows-gui"  set EXTRAS=build,gui

echo === Building ios-enroll (target: %TARGET%) for Windows with Nuitka ===

REM Create build venv if missing
if not exist .venv-build (
    python -m venv .venv-build
)

call .venv-build\Scripts\activate.bat

REM Upgrade pip and install build dependencies
python -m pip install --upgrade pip
python -m pip install -e ".[%EXTRAS%]"

REM Run Nuitka build. Forward --clean if it was the original first
REM arg; the script-level target parsing above has already shifted
REM ``--clean TARGET`` so TARGET is canonical.
if /I "%1"=="--clean" (
    python build_nuitka.py --clean %TARGET%
) else (
    python build_nuitka.py %TARGET%
)

echo.
echo Build complete. Check dist\ for executables.
endlocal
