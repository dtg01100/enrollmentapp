"""Regression tests for build_nuitka.py's Windows compiler selection.

The "Build Executables" CI workflow's Windows job used to force
``--mingw64`` unconditionally, which Nuitka rejects on Python 3.13+
("cannot use '--mingw64' on Python version 3.13 or higher"), failing the
job within seconds. These tests pin the platform-aware behavior:

- native Windows hosts use MSVC (``--msvc=latest``)
- cross-compiles from Linux/macOS keep MinGW on Python <= 3.12
- cross-compiles on Python 3.13+ fail fast with a fix hint
"""
from __future__ import annotations

import sys

import pytest

import build_nuitka


def test_windows_compiler_args_uses_msvc_on_native_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python 3.13+ on Windows must use MSVC, not MinGW."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    assert build_nuitka._windows_compiler_args() == ["--msvc=latest"]


def test_windows_compiler_args_keeps_mingw_cross_compile_on_py312(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinGW cross-compile from Linux is still valid on Python <= 3.12."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "version_info", (3, 12, 0))
    assert build_nuitka._windows_compiler_args() == ["--mingw64"]


def test_windows_compiler_args_rejects_mingw_cross_compile_on_py313(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cross-compiling from a 3.13+ host fails fast with a fix hint."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    with pytest.raises(SystemExit) as exc_info:
        build_nuitka._windows_compiler_args()
    assert exc_info.value.code == 1
    assert "3.13" in capsys.readouterr().err


def _capture_nuitka_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Stub run_nuitka to record each invocation and return success."""
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(build_nuitka, "run_nuitka", fake_run)
    return calls


def test_build_windows_cli_forwards_selected_compiler_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows builders thread the platform-selected flag into Nuitka."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls = _capture_nuitka_calls(monkeypatch)
    assert build_nuitka.build_windows_cli() == 0
    joined = " ".join(calls[0])
    assert "--msvc=latest" in joined
    assert "--mingw64" not in joined


def test_build_windows_gui_forwards_selected_compiler_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GUI builder also carries the platform-selected compiler flag."""
    monkeypatch.setattr(sys, "platform", "win32")
    calls = _capture_nuitka_calls(monkeypatch)
    # PySide6 isn't installed in every dev/test environment; stub the
    # guard so the arg assembly can be verified hermetically.
    monkeypatch.setattr(build_nuitka, "require_pyside6", lambda: None)
    assert build_nuitka.build_windows_gui() == 0
    joined = " ".join(calls[0])
    assert "--msvc=latest" in joined
    assert "--mingw64" not in joined
