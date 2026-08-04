@echo off
REM Build ios-enroll for Windows using Nuitka.
REM
REM Usage:
REM   build_windows.bat                  REM build both Windows CLI + GUI
REM   build_windows.bat cli              REM Windows CLI only — no PySide6 install
REM   build_windows.bat gui              REM Windows GUI only — requires PySide6
REM   build_windows.bat --clean [TARGET] REM remove build artifacts first
REM
REM The target is forwarded to build_nuitka.py. Per-target extras are
REM installed so a CLI-only build on a fresh checkout doesn't pull PySide6.
setlocal enabledelayedexpansion

set TARGET=%1
if "%TARGET%"=="" set TARGET=all

REM ``--clean [TARGET]`` — shift the real target to %2.
if /I "%TARGET%"=="--clean" (
    set TARGET=%2
    if "%TARGET%"=="" set TARGET=all
)

REM Decide whether the [gui] extra is needed. The [build] extra (Nuitka
REM + helpers) is always needed because build_nuitka.py runs Nuitka
REM regardless of the target.
set EXTRAS=build
if /I "%TARGET%"=="gui"     set EXTRAS=build,gui
if /I "%TARGET%"=="all"     set EXTRAS=build,gui
if /I "%TARGET%"=="windows" set EXTRAS=build,gui
if /I "%TARGET%"=="windows-gui" set EXTRAS=build,gui

echo === Building ios-enroll (target: %TARGET%) for Windows with Nuitka ===

REM Create build venv if missing
if not exist .venv-build (
    python -m venv .venv-build
)

call .venv-build\Scripts\activate.bat

REM Upgrade pip and install build dependencies
python -m pip install --upgrade pip
python -m pip install -e ".[%EXTRAS%]"

REM Run Nuitka build
python build_nuitka.py %*

echo.
echo Build complete. Check dist\ for executables.
endlocal
