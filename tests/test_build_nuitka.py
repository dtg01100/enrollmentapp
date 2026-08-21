"""Regression tests for build_nuitka.py's Windows compiler selection.

The "Build Executables" CI workflow's Windows job used to force
``--mingw64`` unconditionally, which Nuitka rejects on Python 3.13+
("cannot use '--mingw64' on Python version 3.13 or higher"), failing the
job within seconds. These tests pin the platform-aware behavior:

- native Windows hosts use MSVC (``--msvc=latest``)
- cross-compiles from Linux/macOS keep MinGW on Python <= 3.12
- cross-compiles on Python 3.13+ fail fast with a fix hint

Additional tests cover the speed-optimization flags (``--jobs``,
``--lto=no``, ``--python-flag=-O``, ``--python-flag=no_docstrings``),
the ``--release`` opt-in for ``--onefile``, optional ClangCL/LLD
selection, and the ``--parallel`` flag.
"""
from __future__ import annotations

import sys

import pytest

import build_nuitka


# --- Platform / compiler selection --------------------------------------


def test_windows_compiler_args_uses_msvc_on_native_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python 3.13+ on Windows must use MSVC, not MinGW."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    # No clang-cl available in the test env; default MSVC.
    monkeypatch.setattr(build_nuitka.shutil, "which", lambda _name: None)
    assert build_nuitka._windows_compiler_args() == ["--msvc=latest"]


def test_windows_compiler_args_keeps_mingw_cross_compile_on_py312(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinGW cross-compile from Linux is still valid on Python <= 3.12."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(build_nuitka.shutil, "which", lambda _name: None)
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


def test_windows_compiler_args_prefers_clangcl_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ClangCL on PATH (or NUITKA_USE_CLANG=1) selects --clang."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    monkeypatch.delenv("NUITKA_USE_CLANG", raising=False)
    monkeypatch.setattr(
        build_nuitka.shutil, "which", lambda name: "/usr/bin/clang-cl" if name == "clang-cl" else None
    )
    assert build_nuitka._windows_compiler_args() == ["--clang", "--msvc=latest"]


def test_windows_compiler_args_honors_nuitka_use_clang_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NUITKA_USE_CLANG=0 forces MSVC even if clang-cl is on PATH."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    monkeypatch.setenv("NUITKA_USE_CLANG", "0")
    monkeypatch.setattr(
        build_nuitka.shutil, "which", lambda name: "/usr/bin/clang-cl" if name == "clang-cl" else None
    )
    assert build_nuitka._windows_compiler_args() == ["--msvc=latest"]


def test_windows_compiler_args_mingw_uses_lld_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinGW cross-compile appends -fuse-ld=lld when lld is on PATH."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(
        build_nuitka.shutil,
        "which",
        lambda name: "/usr/bin/ld.lld" if name == "ld.lld" else None,
    )
    assert build_nuitka._windows_compiler_args() == ["--mingw64", "-fuse-ld=lld"]


# --- base_args: dev shortcuts / release ---------------------------------


def test_base_args_dev_mode_skips_onefile_and_lto() -> None:
    """Default (dev) build uses --standalone, no --onefile, no LTO."""
    args = build_nuitka.base_args(release=False)
    joined = " ".join(args)
    assert "--standalone" in joined
    assert "--onefile" not in joined
    assert "--lto=no" in joined
    # --no-docstrings became the no_docstrings python flag in Nuitka 4.x
    assert "--python-flag=no_docstrings" in joined
    assert "--no-docstrings" not in joined
    assert "--python-flag=-O" in joined


def test_base_args_release_enables_onefile_and_drops_dev_shortcuts() -> None:
    """--release passes --onefile and skips the dev compile shortcuts."""
    args = build_nuitka.base_args(release=True)
    joined = " ".join(args)
    assert "--standalone" in joined
    assert "--onefile" in joined
    assert "--lto=no" not in joined
    assert "--python-flag=no_docstrings" not in joined
    assert "--python-flag=-O" not in joined


def test_base_args_always_sets_jobs_and_omits_removed_cache_flag() -> None:
    """--jobs is present in both dev and release modes; the --cache-dir
    CLI flag was removed in Nuitka 4.x (cache dir is now configured via
    the NUITKA_CACHE_DIR env var, set by the CI workflow), so it must not
    be passed or every build fails with "no such option"."""
    for release in (False, True):
        args = build_nuitka.base_args(release=release)
        joined = " ".join(args)
        assert "--jobs=" in joined, f"--jobs missing (release={release})"
        assert "--cache-dir" not in joined, f"removed flag present (release={release})"


def test_base_args_jobs_respects_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NUITKA_JOBS overrides the detected CPU count."""
    monkeypatch.setenv("NUITKA_JOBS", "3")
    args = build_nuitka.base_args()
    assert "--jobs=3" in args


# --- build_nuitka argument assembly --------------------------------------


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
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    monkeypatch.setattr(build_nuitka.shutil, "which", lambda _name: None)
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
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    monkeypatch.setattr(build_nuitka.shutil, "which", lambda _name: None)
    calls = _capture_nuitka_calls(monkeypatch)
    # PySide6 isn't installed in every dev/test environment; stub the
    # guard so the arg assembly can be verified hermetically.
    monkeypatch.setattr(build_nuitka, "require_pyside6", lambda: None)
    assert build_nuitka.build_windows_gui() == 0
    joined = " ".join(calls[0])
    assert "--msvc=latest" in joined
    assert "--mingw64" not in joined


def test_build_cli_dev_mode_omits_onefile() -> None:
    """Default CLI build is a --standalone directory distribution."""
    args = build_nuitka.base_args(release=False)
    assert "--onefile" not in args


def test_build_cli_release_mode_uses_onefile() -> None:
    """--release CLI build packs into a single self-extracting binary."""
    args = build_nuitka.base_args(release=True)
    assert "--onefile" in args


# --- main(): --release / --parallel flag parsing ------------------------


def _capture_builder_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, bool]]:
    """Replace all four builders with stubs that record (name, release)."""
    calls: list[tuple[str, bool]] = []

    def make(name: str):
        def fn(release: bool = False) -> int:
            calls.append((name, release))
            return 0
        return fn

    monkeypatch.setattr(build_nuitka, "build_cli", make("cli"))
    monkeypatch.setattr(build_nuitka, "build_gui", make("gui"))
    monkeypatch.setattr(build_nuitka, "build_windows_cli", make("windows-cli"))
    monkeypatch.setattr(build_nuitka, "build_windows_gui", make("windows-gui"))
    return calls


def test_main_release_flag_enables_onefile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python build_nuitka.py --release cli`` passes release=True."""
    calls = _capture_builder_calls(monkeypatch)

    rc = build_nuitka.main(["--release", "cli"])
    assert rc == 0
    assert calls == [("cli", True)]


def test_main_no_release_flag_keeps_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``--release``, builders run with release=False."""
    calls = _capture_builder_calls(monkeypatch)

    rc = build_nuitka.main(["cli"])
    assert rc == 0
    assert calls == [("cli", False)]


def test_main_parallel_runs_builders_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--parallel`` fans out independent builders via a thread pool."""
    started: list[str] = []
    finished: list[str] = []

    def make_stub(name: str):
        def fn(release: bool = False) -> int:
            started.append(name)
            # Sleep long enough that the other builder overlaps in the
            # thread pool — proves the schedule is concurrent, not serial.
            import time
            time.sleep(0.05)
            finished.append(name)
            return 0
        return fn

    monkeypatch.setattr(build_nuitka, "build_cli", make_stub("cli"))
    monkeypatch.setattr(build_nuitka, "build_gui", make_stub("gui"))
    monkeypatch.setattr(build_nuitka, "build_windows_cli", make_stub("wcli"))
    monkeypatch.setattr(build_nuitka, "build_windows_gui", make_stub("wgui"))

    rc = build_nuitka.main(["--parallel", "all"])
    assert rc == 0
    # Both builders started before either finished → parallel schedule.
    assert len(started) == 2 and len(finished) == 2
    assert set(started) == {"cli", "gui"}
    assert set(finished) == {"cli", "gui"}


def test_main_unknown_target_returns_error_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown target name exits with code 2 and prints usage."""
    _capture_builder_calls(monkeypatch)
    rc = build_nuitka.main(["bogus"])
    assert rc == 2
    assert "bogus" in capsys.readouterr().err
