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
