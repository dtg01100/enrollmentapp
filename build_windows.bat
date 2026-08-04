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

REM Decide whether the [gui] extra is needed.
set EXTRAS=
if /I "%TARGET%"=="gui"     set EXTRAS=gui
if /I "%TARGET%"=="all"     set EXTRAS=gui
if /I "%TARGET%"=="windows" set EXTRAS=gui
if /I "%TARGET%"=="windows-gui" set EXTRAS=gui

echo === Building ios-enroll (target: %TARGET%) for Windows with Nuitka ===

REM Create build venv if missing
if not exist .venv-build (
    python -m venv .venv-build
)

call .venv-build\Scripts\activate.bat

REM Upgrade pip and install build dependencies
python -m pip install --upgrade pip

if defined EXTRAS (
    python -m pip install -e ".[%EXTRAS%]"
) else (
    python -m pip install -e .
)

python -m pip install nuitka ordered-set zstandard

REM Run Nuitka build
python build_nuitka.py %*

echo.
echo Build complete. Check dist\ for executables.
endlocal
