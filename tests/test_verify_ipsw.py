"""Tests for on-demand IPSW hash verification (ipsw.me)."""

from __future__ import annotations

import json

import pytest

from apple_device_cli.restore.engine import (
    VerifyResult,
    cached_ipsw_path,
    hash_ipsw,
    lookup_ipsw_me,
    parse_ipsw_filename,
    verify_ipsw,
)

SAMPLE_IPSW_ME = {
    "name": "iPad Pro 11-inch (M2)",
    "identifier": "iPad15,7",
    "boards": ["J481AP"],
    "boardconfig": "j481ap",
    "platform": "iPadOS",
    "cpid": 0x8120,
    "bdid": 0x10,
    "firmwares": [
        {
            "version": "26.6",
            "buildid": "23G71",
            "sha1sum": "3c3b614bf258c1e38d5419b40e81cf91774001f0",
            "sha256sum": "a" * 64,
            "md5sum": "e7622c56e52a12250ef179746a1bb7f5",
            "url": "https://updates.cdn-apple.com/2026SummerFCS/fullrestores/140-58697/CB8C4B63-F794-4EED-AD2F-15297E14B69E/iPad15,7_26.6_23G71_Restore.ipsw",
            "filesize": 9943802586,
            "signed": True,
            "releasedate": "2026-07-18T00:00:00Z",
        }
    ],
}


def _mock_urlopen(monkeypatch, payload: dict | None, raises: Exception | None = None):
    import io


    class FakeResp:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode() if payload is not None else b""

        def __enter__(self):
            return io.BytesIO(self._body)

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        if raises is not None:
            raise raises
        return FakeResp(payload)

    monkeypatch.setattr("apple_device_cli.restore.engine.urlopen", fake_urlopen)


class TestParseIpswFilename:
    def test_happy(self):
        assert parse_ipsw_filename("iPad15,7_26.6_23G71_Restore.ipsw") == (
            "iPad15,7",
            "26.6",
            "23G71",
        )

    def test_other_device(self):
        assert parse_ipsw_filename("iPhone17,2_18.5_22F76_Restore.ipsw") == (
            "iPhone17,2",
            "18.5",
            "22F76",
        )

    def test_rejects_garbage(self):
        assert parse_ipsw_filename("random-file.bin") is None
        assert parse_ipsw_filename("") is None
        assert parse_ipsw_filename("iPad15,7_26.6_23G71.bin") is None


class TestCachedIpswPath:
    def test_hit(self, tmp_path):
        f = tmp_path / "iPad15,7_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"x")
        assert cached_ipsw_path(
            "https://cdn.example/iPad15,7_26.6_23G71_Restore.ipsw", tmp_path
        ) == f

    def test_miss(self, tmp_path):
        assert (
            cached_ipsw_path(
                "https://cdn.example/iPad15,7_26.6_23G71_Restore.ipsw", tmp_path
            )
            is None
        )

    def test_zero_size_is_miss(self, tmp_path):
        f = tmp_path / "iPad15,7_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"")
        assert (
            cached_ipsw_path(
                "https://cdn.example/iPad15,7_26.6_23G71_Restore.ipsw", tmp_path
            )
            is None
        )

    def test_bare_filename(self, tmp_path):
        f = tmp_path / "iPad15,7_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"x")
        assert cached_ipsw_path("iPad15,7_26.6_23G71_Restore.ipsw", tmp_path) == f


class TestHashIpsw:
    def test_sha1_known_vector(self, tmp_path):
        f = tmp_path / "small.bin"
        f.write_bytes(b"hello world")
        assert hash_ipsw(f, "sha1") == "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"

    def test_sha256(self, tmp_path):
        f = tmp_path / "small.bin"
        f.write_bytes(b"hello world")
        assert hash_ipsw(f, "sha256") == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            hash_ipsw(tmp_path / "nope.bin")


class TestLookupIpswMe:
    def test_matches_build(self, monkeypatch):
        _mock_urlopen(monkeypatch, SAMPLE_IPSW_ME)
        ident = lookup_ipsw_me("iPad15,7", build="23G71")
        assert ident is not None
        assert ident.build == "23G71"
        assert ident.sha1sum == "3c3b614bf258c1e38d5419b40e81cf91774001f0"
        assert ident.filesize == 9943802586
        assert ident.device == "iPad15,7"

    def test_matches_version_when_build_none(self, monkeypatch):
        _mock_urlopen(monkeypatch, SAMPLE_IPSW_ME)
        ident = lookup_ipsw_me("iPad15,7", version="26.6")
        assert ident is not None
        assert ident.build == "23G71"

    def test_network_error_returns_none(self, monkeypatch):
        from urllib.error import URLError

        _mock_urlopen(monkeypatch, None, raises=URLError("boom"))
        assert lookup_ipsw_me("iPad15,7", build="23G71") is None

    def test_unknown_build_returns_none(self, monkeypatch):
        _mock_urlopen(monkeypatch, SAMPLE_IPSW_ME)
        assert lookup_ipsw_me("iPad15,7", build="ZZZZZ") is None


class TestVerifyIpsw:
    def test_full_match(self, tmp_path, monkeypatch):
        f = tmp_path / "iPad15,7_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"hello world")
        _mock_urlopen(monkeypatch, SAMPLE_IPSW_ME)
        # Patch the expected hashes to match the local file's actual hashes.
        import apple_device_cli.restore.engine as engine

        local_sha1 = engine.hash_ipsw(f, "sha1")
        local_sha256 = engine.hash_ipsw(f, "sha256")
        payload = json.loads(json.dumps(SAMPLE_IPSW_ME))
        payload["firmwares"][0]["sha1sum"] = local_sha1
        payload["firmwares"][0]["sha256sum"] = local_sha256
        payload["firmwares"][0]["filesize"] = f.stat().st_size
        _mock_urlopen(monkeypatch, payload)

        result = verify_ipsw(f)
        assert isinstance(result, VerifyResult)
        assert result.sha1_match is True
        assert result.sha256_match is True
        assert result.size_match is True
        assert "VERIFIED" in result.summary

    def test_mismatch(self, tmp_path, monkeypatch):
        f = tmp_path / "iPad15,7_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"hello world")
        _mock_urlopen(monkeypatch, SAMPLE_IPSW_ME)
        result = verify_ipsw(f)
        assert result.sha1_match is False
        assert "MISMATCH" in result.summary

    def test_unknown_firmware_still_hashes(self, tmp_path):
        f = tmp_path / "random-file.bin"
        f.write_bytes(b"hello world")
        result = verify_ipsw(f)
        assert result.expected is None
        assert result.local_sha1 == "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"
        assert result.local_size == 11

    def test_lookup_failure_expected_none(self, tmp_path, monkeypatch):
        from urllib.error import URLError

        f = tmp_path / "iPad15,7_26.6_23G71_Restore.ipsw"
        f.write_bytes(b"hello world")
        _mock_urlopen(monkeypatch, None, raises=URLError("boom"))
        result = verify_ipsw(f)
        assert result.expected is None
        assert result.sha1_match is None
