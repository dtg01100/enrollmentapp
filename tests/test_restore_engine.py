"""Tests for restore/engine.py."""
from __future__ import annotations

import pytest

from apple_device_cli.restore.engine import (
    SignedVersion,
    parse_ipsw_url,
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
        from unittest.mock import MagicMock
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
