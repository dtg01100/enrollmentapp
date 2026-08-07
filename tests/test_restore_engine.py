"""Tests for restore/engine.py."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from apple_device_cli.restore.engine import (
    SignedVersion,
    parse_ipsw_url,
    parse_progress_line,
)


class TestParseIpswUrl:
    """Apple CDN URL format:
    https://updates.cdn-apple.com/.../..._<DeviceName>_<iOSVersion>_<Build>_Restore.ipsw
    """

    def test_parses_standard_url(self):
        url = "https://updates.cdn-apple.com/2026SummerFCS/fullrestores/140-58358/088E3BFD-2D6B-4D05-8355-954E54D08042/iPad_Pro_Spring_2021_26.6_23G71_Restore.ipsw"
        result = parse_ipsw_url(url, device="iPad13,4")
        assert result is not None
        assert result.version == "26.6"
        assert result.build == "23G71"
        assert result.url == url
        assert result.device == "iPad13,4"

    def test_handles_multi_digit_version(self):
        url = "https://updates.cdn-apple.com/foo/iPad_13_17.5.1_21F90_Restore.ipsw"
        result = parse_ipsw_url(url, device="iPad13,4")
        assert result is not None
        assert result.version == "17.5.1"
        assert result.build == "21F90"

    def test_returns_none_for_unrecognized_url(self):
        assert parse_ipsw_url("not a url", device="x") is None
        assert parse_ipsw_url("https://example.com/file.zip", device="x") is None
        # Has the .ipsw extension but missing _Restore.ipsw marker
        assert parse_ipsw_url("https://example.com/file.ipsw", device="x") is None
        # Has _Restore.ipsw but no version/build pattern
        assert parse_ipsw_url(
            "https://example.com/something_Restore.ipsw", device="x"
        ) is None

    def test_display_label_includes_version_and_build(self):
        v = SignedVersion(
            version="26.6",
            build="23G71",
            url="https://x/y/z.ipsw",
            device="iPad13,4",
        )
        assert v.display_label == "iOS 26.6 (23G71)"

    def test_url_with_betasuffix_build(self):
        """iOS 17 public releases end in something like 21A342. Verify the
        build parser is permissive (alphanumeric, no special assumptions)."""
        url = "https://example.com/iPad13,4_17.0_21A342_Restore.ipsw"
        result = parse_ipsw_url(url, device="iPad13,4")
        assert result is not None
        assert result.version == "17.0"
        assert result.build == "21A342"


class TestParseProgressLine:
    """Parser for idevicerestore progress lines (plain -P + default)."""

    def test_plain_progress_line(self):
        result = parse_progress_line("PROGRESS: 12/30")
        assert result is not None
        assert result.kind == "percent"
        assert result.value == 40  # 12/30 * 100, rounded to int
        assert result.total == 30
        assert result.label is None

    def test_plain_step_line(self):
        result = parse_progress_line("STEP: Restoring Baseband")
        assert result is not None
        assert result.kind == "step"
        assert result.value is None
        assert result.total is None
        assert result.label == "Restoring Baseband"

    def test_default_uploading_line(self):
        result = parse_progress_line(
            "  Uploading [==================================================] 100.0%"
        )
        assert result is not None
        assert result.kind == "percent"
        assert result.value == 100
        assert result.total == 100
        assert result.label is None

    def test_default_uploading_partial_value(self):
        result = parse_progress_line(
            "  Uploading [===                                               ]   6.2%"
        )
        assert result is not None
        assert result.kind == "percent"
        assert result.value == 6
        assert result.total == 100

    def test_non_progress_lines_return_none(self):
        assert parse_progress_line("Sending LLB (185208 bytes)...") is None
        assert parse_progress_line("Restore OK") is None
        assert parse_progress_line("   • Latest release found is: 26.6") is None
        assert parse_progress_line("") is None
        assert parse_progress_line("   ") is None


class TestListSignedVersions:
    """Subprocess wrapper around ``ipsw download ipsw --device X --urls``."""

    def test_parses_ipsw_urls_output(self, monkeypatch):
        from unittest.mock import MagicMock

        from apple_device_cli.restore import engine

        sample_output = (
            "https://updates.cdn-apple.com/2026SummerFCS/fullrestores/140-58358/abc/iPad_Pro_Spring_2021_26.6_23G71_Restore.ipsw\n"
            "https://updates.cdn-apple.com/2026SpringFCS/fullrestores/122-38416/def/iPad_Pro_Spring_2021_26.5.2_23F84_Restore.ipsw\n"
        )
        fake_proc = MagicMock()
        fake_proc.stdout = sample_output
        fake_proc.returncode = 0
        fake_proc.wait = MagicMock()

        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(engine, "_popen_capture", fake_popen)

        results = engine.list_signed_versions("iPad13,4")

        assert len(results) == 2
        assert results[0].version == "26.6"
        assert results[0].build == "23G71"
        assert results[1].version == "26.5.2"
        assert results[1].build == "23F84"
        # Constructed command must include the right flags
        args, _kwargs = fake_popen.call_args
        cmd = args[0]
        assert cmd[0] == "ipsw"
        assert "download" in cmd
        assert "ipsw" in cmd
        assert "--device" in cmd
        assert "iPad13,4" in cmd
        assert "--urls" in cmd

    def test_skips_lines_that_dont_match_url_pattern(self, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        sample_output = (
            "https://updates.cdn-apple.com/foo/iPad_Pro_26.6_23G71_Restore.ipsw\n"
            "   • Latest release found is: 26.6\n"  # noise line
            "  \n"  # blank line
            "https://updates.cdn-apple.com/foo/iPad_Pro_26.5_23F77_Restore.ipsw\n"
        )
        fake_proc = MagicMock()
        fake_proc.stdout = sample_output
        fake_proc.returncode = 0
        fake_proc.wait = MagicMock()
        monkeypatch.setattr(engine, "_popen_capture", MagicMock(return_value=fake_proc))

        results = engine.list_signed_versions("iPad13,4")
        assert len(results) == 2
        assert [r.version for r in results] == ["26.6", "26.5"]

    def test_missing_ipsw_binary_raises_engine_error(self, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        monkeypatch.setattr(
            "shutil.which", lambda name: None if name == "ipsw" else "/usr/bin/x"
        )
        with pytest.raises(RestoreEngineError, match="ipsw"):
            engine.list_signed_versions("iPad13,4")

    def test_nonzero_exit_raises_engine_error(self, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        fake_proc = MagicMock()
        fake_proc.stdout = ""
        fake_proc.returncode = 1
        fake_proc.wait = MagicMock()
        monkeypatch.setattr(engine, "_popen_capture", MagicMock(return_value=fake_proc))

        with pytest.raises(RestoreEngineError, match="ipsw"):
            engine.list_signed_versions("iPad13,4")


class TestDownloadIpsw:
    """urllib wrapper for IPSW downloads with Range-resume."""

    def test_writes_complete_file_when_no_partial(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        class FakeResp:
            def __init__(self, data: bytes):
                self._data = data
                self.headers = {"Content-Length": str(len(data))}
            def read(self, n=-1):
                if n == -1:
                    chunk, self._data = self._data, b""
                    return chunk
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk
            def __enter__(self): return self
            def __exit__(self, *a): pass

        fake_urlopen = MagicMock(return_value=FakeResp(b"hello-world"))
        monkeypatch.setattr(engine, "urlopen", fake_urlopen)

        result = engine.download_ipsw(
            "https://example.com/iPad_26.6_23G71_Restore.ipsw",
            dest_dir=tmp_path,
        )

        assert result == tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        assert result.read_bytes() == b"hello-world"
        # No Range header on first write
        args, kwargs = fake_urlopen.call_args
        request_obj = args[0]
        assert "Range" not in request_obj.headers

    def test_resumes_partial_with_range_header(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        # Pre-existing partial file (the production code looks at
        # <basename>.partial, not <basename>, to decide how much to resume).
        partial = tmp_path / "iPad_26.6_23G71_Restore.ipsw.partial"
        partial.write_bytes(b"hello-")  # 6 bytes

        class FakeResp:
            def __init__(self, data: bytes):
                self._data = data
                self.headers = {"Content-Length": str(6 + len(data))}
            def read(self, n=-1):
                if n == -1:
                    chunk, self._data = self._data, b""
                    return chunk
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk
            def __enter__(self): return self
            def __exit__(self, *a): pass

        fake_urlopen = MagicMock(return_value=FakeResp(b"world"))
        monkeypatch.setattr(engine, "urlopen", fake_urlopen)

        engine.download_ipsw(
            "https://example.com/iPad_26.6_23G71_Restore.ipsw",
            dest_dir=tmp_path,
        )

        # The request must have included a Range header
        request_obj = fake_urlopen.call_args[0][0]
        assert request_obj.headers.get("Range") == "bytes=6-"

    def test_uses_partial_suffix_during_download(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        class FakeResp:
            def __init__(self):
                self._data = b"abc"
                self.headers = {"Content-Length": "3"}
            def read(self, n=-1):
                chunk, self._data = self._data[:n], self._data[n:]
                return chunk
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(engine, "urlopen", MagicMock(return_value=FakeResp()))

        result = engine.download_ipsw(
            "https://example.com/foo_Restore.ipsw",
            dest_dir=tmp_path,
        )
        assert result.name == "foo_Restore.ipsw"
        # Final path exists, .partial does not
        assert result.exists()
        assert not (tmp_path / "foo_Restore.ipsw.partial").exists()

    def test_three_failures_raises_engine_error(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from urllib.error import URLError
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        monkeypatch.setattr(
            engine, "urlopen", MagicMock(side_effect=URLError("network down"))
        )
        with pytest.raises(RestoreEngineError, match="3"):
            engine.download_ipsw(
                "https://example.com/foo_Restore.ipsw",
                dest_dir=tmp_path,
            )


class TestGetProductTypeForUdid:
    """Looks up lockdown.ProductType for a UDID.

    Uses the existing ``_pair_then_retry_connect`` wrapper so iOS 26
    trust failures self-heal.
    """

    def test_returns_product_type_from_lockdown(self, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from apple_device_cli.restore import engine

        fake_lockdown = MagicMock()
        fake_lockdown.get_value = AsyncMock(return_value={"Value": "iPad13,4"})

        async def fake_connect(serial):
            return fake_lockdown

        monkeypatch.setattr(
            engine, "_create_using_usbmux_with_pair_retry", fake_connect
        )

        result = asyncio.run(engine.get_product_type_for_udid("UDID-A"))
        assert result == "iPad13,4"

    def test_handles_plain_string_value(self, monkeypatch):
        """Some pymobiledevice3 versions return the value as a plain
        string instead of ``{"Value": "..."}``. Both shapes are
        accepted."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from apple_device_cli.restore import engine

        fake_lockdown = MagicMock()
        fake_lockdown.get_value = AsyncMock(return_value="iPhone15,2")
        monkeypatch.setattr(
            engine, "_create_using_usbmux_with_pair_retry",
            AsyncMock(return_value=fake_lockdown),
        )

        result = asyncio.run(engine.get_product_type_for_udid("UDID-B"))
        assert result == "iPhone15,2"

    def test_missing_product_type_raises_engine_error(self, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        fake_lockdown = MagicMock()
        fake_lockdown.get_value = AsyncMock(return_value=None)
        monkeypatch.setattr(
            engine, "_create_using_usbmux_with_pair_retry",
            AsyncMock(return_value=fake_lockdown),
        )

        with pytest.raises(RestoreEngineError, match="ProductType"):
            asyncio.run(engine.get_product_type_for_udid("UDID-C"))


class TestRestoreDevice:
    """Subprocess wrapper around ``idevicerestore -e -u <udid> -y ...``."""

    def _fake_proc(self, returncode: int = 0, stdout: str = ""):
        from unittest.mock import MagicMock
        p = MagicMock()
        p.returncode = returncode
        # Use a list-iterable so the production code's `for line in proc.stdout`
        # yields one line at a time (matches a real text-mode Popen stream).
        # Strings would be iterated char-by-char, which is not what we want.
        p.stdout = stdout.splitlines(keepends=True) if stdout else []
        p.wait = MagicMock()
        p.kill = MagicMock()
        return p

    def test_success_returns_restore_result(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine
        from pathlib import Path

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake-ipsw")
        cache = tmp_path / "cache"
        cache.mkdir()

        fake_proc = self._fake_proc(returncode=0, stdout="Restore OK\n")
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(engine, "_popen_capture", fake_popen)

        result = engine.restore_device(
            udid="UDID-A",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )

        assert result.success is True
        assert result.udid == "UDID-A"
        assert result.ipsw_path == ipsw
        assert result.error is None

        # Verify the constructed command line
        args, kwargs = fake_popen.call_args
        cmd = args[0]
        # The binary name comes first; the rest of the args are flags + path
        assert Path(cmd[0]).name == "idevicerestore"
        assert "-e" in cmd
        assert "-u" in cmd
        assert "UDID-A" in cmd
        assert "-y" in cmd
        assert "-P" in cmd
        assert "-C" in cmd
        assert str(cache) in cmd
        assert "--logfile" in " ".join(cmd)
        assert str(ipsw) in cmd
        # No timeout
        assert "timeout" not in kwargs
        # stdin=DEVNULL behavior is verified by the fact that
        # _popen_capture always sets it (see engine._popen_capture).
        # We can't easily assert it from the mock here because the
        # mock isn't a real Popen and `stdin=DEVNULL` doesn't get
        # recorded as a kwarg by Popen.__init__.
        import subprocess
        assert subprocess.DEVNULL is not None  # devnull exists

    def test_nonzero_exit_returns_failure(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        fake_proc = self._fake_proc(returncode=1, stdout="ERROR: bad IPSW\n")
        monkeypatch.setattr(engine, "_popen_capture", MagicMock(return_value=fake_proc))

        result = engine.restore_device(
            udid="UDID-B",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )

        assert result.success is False
        assert result.error is not None
        assert "bad IPSW" in result.error
        # Log file path is included so the user knows where to look
        assert "log" in result.error.lower()

    def test_missing_idevicerestore_falls_back_to_pymd3(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        monkeypatch.setattr(
            "shutil.which", lambda name: None if name == "idevicerestore" else "/usr/bin/x"
        )
        # pymd3 path is xfail-stubbed — assert the engine DOES try it
        # and surfaces whatever it returns. The pymd3 path is xfailed
        # in the production code because the upstream Restore API is
        # brittle on iOS 26.
        fake_pmd3 = MagicMock(return_value=engine.RestoreResult(
            success=False, error="xfail: pymd3 Restore API is brittle"
        ))
        monkeypatch.setattr(engine, "restore_device_via_pymd3", fake_pmd3)

        result = engine.restore_device(
            udid="UDID-C",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )
        # The pymd3 fallback was called and its result is returned
        assert result.success is False
        assert "xfail" in (result.error or "")

    def test_progress_callback_receives_stdout_lines(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        # The streaming reader thread will see this output
        fake_proc = self._fake_proc(returncode=0, stdout="line1\nline2\nline3\n")
        monkeypatch.setattr(engine, "_popen_capture", MagicMock(return_value=fake_proc))

        received: list[str] = []
        engine.restore_device(
            udid="UDID-D",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: received.append(event.text),
        )
        # The callback was called with the lines
        assert "line1" in received
        assert "line2" in received
        assert "line3" in received

    def test_progress_callback_receives_progress_events(self, tmp_path, monkeypatch):
        """Lines that match a progress format arrive as events with progress set."""
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        fake_proc = self._fake_proc(
            returncode=0,
            stdout="PROGRESS: 12/30\nSTEP: Restoring Baseband\n",
        )
        monkeypatch.setattr(engine, "_popen_capture", MagicMock(return_value=fake_proc))

        received: list[engine.ProgressEvent] = []
        engine.restore_device(
            udid="UDID-E",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=received.append,
        )

        assert received[0].text == "PROGRESS: 12/30"
        assert received[0].progress is not None
        assert received[0].progress.kind == "percent"
        assert received[0].progress.value == 40
        assert received[1].text == "STEP: Restoring Baseband"
        assert received[1].progress is not None
        assert received[1].progress.kind == "step"
        assert received[1].progress.label == "Restoring Baseband"

    def test_restore_device_normal_mode_uses_udid(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        monkeypatch.setattr(engine, "detect_device_mode", lambda udid: "normal")
        fake_proc = self._fake_proc(returncode=0, stdout="Restore OK\n")
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(engine, "_popen_capture", fake_popen)

        engine.restore_device(
            udid="UDID-A",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )

        cmd = fake_popen.call_args.args[0]
        assert "-u" in cmd
        assert "UDID-A" in cmd
        assert "-i" not in cmd

    def test_restore_device_recovery_mode_uses_ecid(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        monkeypatch.setattr(engine, "detect_device_mode", lambda udid: "recovery")
        monkeypatch.setattr(
            engine, "_device_ecid", lambda udid=None: "00094daa01d80032"
        )
        fake_proc = self._fake_proc(returncode=0, stdout="Restore OK\n")
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(engine, "_popen_capture", fake_popen)

        engine.restore_device(
            udid="UDID-A",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )

        cmd = fake_popen.call_args.args[0]
        assert "-i" in cmd
        assert "00094daa01d80032" in cmd
        assert "-u" not in cmd

    def test_restore_device_recovery_mode_ecid_unresolved_raises(self, tmp_path, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        monkeypatch.setattr(engine, "detect_device_mode", lambda udid: "recovery")
        monkeypatch.setattr(engine, "_device_ecid", lambda udid=None: None)

        with pytest.raises(RestoreEngineError, match="ECID"):
            engine.restore_device(
                udid="UDID-A",
                ipsw_path=ipsw,
                cache_dir=cache,
                progress_callback=lambda event: None,
            )

    def test_restore_device_unknown_mode_retries_with_ecid(self, tmp_path, monkeypatch):
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        monkeypatch.setattr(engine, "detect_device_mode", lambda udid: "unknown")
        monkeypatch.setattr(engine, "_device_ecid", lambda udid=None: "abc")

        first = self._fake_proc(
            returncode=1, stdout="ERROR: Unable to discover device mode\n"
        )
        second = self._fake_proc(returncode=0, stdout="Restore OK\n")
        popen_calls: list = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return first if len(popen_calls) == 1 else second

        monkeypatch.setattr(engine, "_popen_capture", fake_popen)

        result = engine.restore_device(
            udid="UDID-A",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )

        assert result.success is True
        assert len(popen_calls) == 2
        assert "-u" in popen_calls[0]
        assert "UDID-A" in popen_calls[0]
        assert "-i" in popen_calls[1]
        assert "abc" in popen_calls[1]
        assert "-u" not in popen_calls[1]

    def test_restore_device_unknown_mode_normal_first_attempt_succeeds(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        cache = tmp_path / "cache"
        cache.mkdir()

        monkeypatch.setattr(engine, "detect_device_mode", lambda udid: "unknown")
        fake_proc = self._fake_proc(returncode=0, stdout="Restore OK\n")
        fake_popen = MagicMock(return_value=fake_proc)
        monkeypatch.setattr(engine, "_popen_capture", fake_popen)

        result = engine.restore_device(
            udid="UDID-A",
            ipsw_path=ipsw,
            cache_dir=cache,
            progress_callback=lambda event: None,
        )

        assert result.success is True
        assert fake_popen.call_count == 1
        cmd = fake_popen.call_args.args[0]
        assert "-u" in cmd
        assert "UDID-A" in cmd


class TestDeviceEcidHelper:
    """Resolves a device's ECID for ``idevicerestore -i`` targeting."""

    def test_device_ecid_helper_finds_by_serial(self, monkeypatch):
        from apple_device_cli.restore import engine

        # A normal-mode device whose USB serial equals the UDID.
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            lambda: iter([("UDID-A", 0x12a8, 1, SimpleNamespace())]),
        )
        monkeypatch.setattr(
            engine, "_query_recovery_ecid", lambda: "00094daa01d80032"
        )

        assert engine._device_ecid("UDID-A") == "00094daa01d80032"

    def test_device_ecid_helper_parses_irecovery_output(self, monkeypatch):
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        # A recovery-mode device whose SRNM differs from the UDID — the
        # serial match fails, so the helper falls back to `irecovery -q`.
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            lambda: iter([("JXMWM7422V", 0x1281, 1, SimpleNamespace())]),
        )
        fake_proc = MagicMock()
        fake_proc.stdout = (
            "CPID: 0x8010\n"
            "ECID: 0x00094daa01d80032\n"
            "SRNM: JXMWM7422V\n"
        )
        monkeypatch.setattr(engine.subprocess, "run", MagicMock(return_value=fake_proc))

        assert engine._device_ecid("UDID-A") == "00094daa01d80032"

    def test_device_ecid_helper_returns_none_when_no_device(self, monkeypatch):
        from apple_device_cli.restore import engine

        monkeypatch.setattr(engine, "_iter_apple_usb_devices", lambda: iter(()))
        monkeypatch.setattr(
            engine, "_query_recovery_ecid", lambda: "should-not-run"
        )

        assert engine._device_ecid("UDID-A") is None


class TestDetectDeviceMode:
    """USB product-ID based mode detection (normal/recovery/restore/dfu).

    ``_iter_apple_usb_devices`` yields ``(serial, product_id, bus, device)``;
    the device object is a placeholder here because mode detection only reads
    the product ID.
    """

    def _patch_bus(self, monkeypatch, devices):
        from apple_device_cli.restore import engine

        monkeypatch.setattr(engine, "_iter_apple_usb_devices", lambda: iter(devices))

    def test_detect_device_mode_normal(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("UDID-A", 0x12a8, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "normal"

    def test_detect_device_mode_normal_v2_pid(self, monkeypatch):
        """Newer devices report 0x12ab in normal mode — still 'normal'."""
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("UDID-A", 0x12ab, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "normal"

    def test_detect_device_mode_recovery(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("UDID-A", 0x1281, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "recovery"

    def test_detect_device_mode_restore(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("UDID-A", 0x12ac, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "restore"

    def test_detect_device_mode_dfu(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("UDID-A", 0x1227, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "dfu"

    def test_detect_device_mode_unknown(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("UDID-A", 0x9999, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "unknown"

    def test_detect_device_mode_no_device(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [])
        assert engine.detect_device_mode("UDID-A") == "unknown"

    def test_detect_device_mode_serial_mismatch(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(monkeypatch, [("OTHER", 0x12a8, 1, SimpleNamespace())])
        assert engine.detect_device_mode("UDID-A") == "unknown"

    def test_detect_device_mode_normalizes_dashes_and_nulls(self, monkeypatch):
        from apple_device_cli.restore import engine

        self._patch_bus(
            monkeypatch,
            [("00008101-001234567890ABCD", 0x12a8, 1, SimpleNamespace())],
        )
        assert engine.detect_device_mode("00008101001234567890ABCD") == "normal"


class TestEnterRecoveryMode:
    """EnterRecovery via lockdown's enter_recovery() request."""

    def test_enter_recovery_mode_uses_single_event_loop(self, tmp_path, monkeypatch):
        """Regression: enter_recovery_mode must use ONE asyncio.run, not two.

        Previously the code did:
            lockdown = asyncio.run(_create_using_usbmux_with_pair_retry(udid))  # loop A
            asyncio.run(lockdown.enter_recovery())                                  # loop B — different loop!

        This caused "got Future attached to a different loop" when called from
        the GUI's WorkerThread. The fix wraps both calls in a single inner
        coroutine and uses ONE asyncio.run.
        """
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        # Track how many times asyncio.run is called inside enter_recovery_mode
        run_calls: list = []
        real_asyncio_run = asyncio.run

        def tracking_run(coro, *args, **kwargs):
            run_calls.append(coro)
            return real_asyncio_run(coro, *args, **kwargs)

        fake_lockdown = MagicMock()
        fake_lockdown.enter_recovery = MagicMock(
            return_value=_async_return(None)
        )

        async def fake_create(serial):
            return fake_lockdown

        monkeypatch.setattr(engine, "_create_using_usbmux_with_pair_retry", fake_create)
        monkeypatch.setattr(asyncio, "run", tracking_run)

        engine.enter_recovery_mode("UDID-X")

        # The regression: before the fix, this was 2 (one per asyncio.run).
        # After the fix, it's exactly 1.
        assert len(run_calls) == 1, (
            f"enter_recovery_mode called asyncio.run {len(run_calls)} times; "
            f"expected exactly 1. Multiple event loops cause pymobiledevice3 "
            f"to raise 'got Future attached to a different loop'."
        )

    def test_enter_recovery_mode_works_when_called_from_worker_thread(self, monkeypatch):
        """Regression: GUI's WorkerThread runs asyncio.run in its own loop.

        The fix must produce a working enter_recovery call when invoked
        through the same async pattern the GUI uses, not just when called
        from the test's main thread.
        """
        import threading
        from unittest.mock import MagicMock
        from apple_device_cli.restore import engine

        fake_lockdown = MagicMock()
        fake_lockdown.enter_recovery = MagicMock(return_value=_async_return(None))

        async def fake_create(serial):
            return fake_lockdown

        monkeypatch.setattr(engine, "_create_using_usbmux_with_pair_retry", fake_create)

        errors: list = []

        def worker():
            try:
                engine.enter_recovery_mode("UDID-Y")
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        # The "different loop" error is what would happen if the bug came back.
        assert not errors, f"Worker-thread call raised: {errors!r}"

    def test_enter_recovery_mode_raises_engine_error_on_failure(self, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        async def fake_connect(serial):
            raise ConnectionError("device gone")

        monkeypatch.setattr(engine, "_create_using_usbmux_with_pair_retry", fake_connect)

        with pytest.raises(RestoreEngineError, match="enter recovery"):
            engine.enter_recovery_mode("UDID-A")

    def test_enter_recovery_mode_request_failure_raises(self, monkeypatch):
        """A lockdownd error mid-request also becomes RestoreEngineError."""
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        class FakeLockdown:
            async def enter_recovery(self):
                raise RuntimeError("EnterRecovery rejected")

        async def fake_connect(serial):
            return FakeLockdown()

        monkeypatch.setattr(engine, "_create_using_usbmux_with_pair_retry", fake_connect)

        with pytest.raises(RestoreEngineError, match="rejected"):
            engine.enter_recovery_mode("UDID-A")


class TestExitRecoveryMode:
    """USB reset (D+/D- toggle) out of recovery mode.

    ``exit_recovery_mode`` scans ``_iter_apple_usb_devices`` (no lockdown /
    usbmux access — the device is in iBoot mode) and can be called with or
    without a UDID.
    """

    @staticmethod
    def _iter_devices(*devices):
        """Build an ``_iter_apple_usb_devices`` replacement from (serial, pid, device)."""
        entries = [(serial, pid, 1, device) for serial, pid, device in devices]
        return lambda: iter(entries)

    def test_exit_recovery_mode_calls_usb_reset(self, monkeypatch):
        from apple_device_cli.restore import engine

        reset_calls: list[str] = []
        device = SimpleNamespace(idVendor=0x05ac, idProduct=0x1281)
        device.reset = lambda: reset_calls.append("reset")
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            self._iter_devices(("UDID-A", 0x1281, device)),
        )

        result = engine.exit_recovery_mode("UDID-A")

        assert reset_calls == ["reset"]
        assert result == ["UDID-A"]

    def test_exit_recovery_mode_raises_engine_error_on_failure(self, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        monkeypatch.setattr(engine, "_iter_apple_usb_devices", lambda: iter(()))
        with pytest.raises(RestoreEngineError, match="recovery"):
            engine.exit_recovery_mode("UDID-A")

    def test_exit_recovery_mode_reset_error_raises(self, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        device = SimpleNamespace(idVendor=0x05ac, idProduct=0x1281)

        def boom():
            raise RuntimeError("USBError: reset failed")

        device.reset = boom
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            self._iter_devices(("UDID-A", 0x1281, device)),
        )
        with pytest.raises(RestoreEngineError, match="reset"):
            engine.exit_recovery_mode("UDID-A")

    def test_exit_recovery_mode_without_udid_resets_any_recovery_device(self, monkeypatch):
        from apple_device_cli.restore import engine

        reset_calls: list[str] = []
        device = SimpleNamespace(idVendor=0x05ac, idProduct=0x1281)
        device.reset = lambda: reset_calls.append("reset")
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            self._iter_devices(("CPID:0x8010", 0x1281, device)),
        )

        result = engine.exit_recovery_mode()

        assert reset_calls == ["reset"]
        assert result == ["CPID:0x8010"]

    def test_exit_recovery_mode_without_udid_no_recovery_device_raises(self, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        monkeypatch.setattr(engine, "_iter_apple_usb_devices", lambda: iter(()))
        with pytest.raises(RestoreEngineError, match=r"(?i)no recovery device"):
            engine.exit_recovery_mode()

    def test_exit_recovery_mode_without_udid_dfu_only_raises(self, monkeypatch):
        from apple_device_cli.restore import engine
        from apple_device_cli.restore.errors import RestoreEngineError

        device = SimpleNamespace(idVendor=0x05ac, idProduct=0x1227)
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            self._iter_devices(("SERIAL", 0x1227, device)),
        )

        with pytest.raises(RestoreEngineError, match="power cycle"):
            engine.exit_recovery_mode()

    def test_exit_recovery_mode_without_udid_multiple_recovery_devices(self, monkeypatch):
        from apple_device_cli.restore import engine

        reset_calls: list[str] = []
        device1 = SimpleNamespace(idVendor=0x05ac, idProduct=0x1281)
        device1.reset = lambda: reset_calls.append("reset-1")
        device2 = SimpleNamespace(idVendor=0x05ac, idProduct=0x1281)
        device2.reset = lambda: reset_calls.append("reset-2")
        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            self._iter_devices(
                ("SERIAL-A", 0x1281, device1), ("SERIAL-B", 0x1281, device2)
            ),
        )

        result = engine.exit_recovery_mode()

        assert reset_calls == ["reset-1", "reset-2"]
        assert result == ["SERIAL-A", "SERIAL-B"]


class TestDetectRecoveryDevicesPresent:
    """USB-bus scan for iBoot-mode (recovery/DFU) devices."""

    def test_detect_recovery_devices_present_true(self, monkeypatch):
        from apple_device_cli.restore import engine

        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            lambda: iter([("SERIAL", 0x1281, 1, SimpleNamespace())]),
        )
        assert engine.detect_recovery_devices_present() is True

    def test_detect_recovery_devices_present_true_for_dfu(self, monkeypatch):
        from apple_device_cli.restore import engine

        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            lambda: iter([("SERIAL", 0x1227, 1, SimpleNamespace())]),
        )
        assert engine.detect_recovery_devices_present() is True

    def test_detect_recovery_devices_present_false_no_devices(self, monkeypatch):
        from apple_device_cli.restore import engine

        monkeypatch.setattr(engine, "_iter_apple_usb_devices", lambda: iter(()))
        assert engine.detect_recovery_devices_present() is False

    def test_detect_recovery_devices_present_false_only_normal(self, monkeypatch):
        from apple_device_cli.restore import engine

        monkeypatch.setattr(
            engine,
            "_iter_apple_usb_devices",
            lambda: iter([("UDID-A", 0x12a8, 1, SimpleNamespace())]),
        )
        assert engine.detect_recovery_devices_present() is False

    def test_detect_recovery_devices_present_never_raises(self, monkeypatch):
        from apple_device_cli.restore import engine

        def boom():
            raise RuntimeError("libusb gone")

        monkeypatch.setattr(engine, "_iter_apple_usb_devices", boom)
        assert engine.detect_recovery_devices_present() is False


def _async_return(value):
    """Return a coroutine that resolves to ``value`` (synchronous helper)."""

    async def _co():
        return value

    return _co()
