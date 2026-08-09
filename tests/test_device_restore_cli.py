"""Tests for the new ``ios-enroll device restore`` CLI subcommand."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from apple_device_cli.cli import app


runner = CliRunner()


class TestDeviceRestoreCLI:
    def test_help_lists_subcommand(self):
        result = runner.invoke(app, ["device", "restore", "--help"])
        assert result.exit_code == 0
        assert "Restore" in result.output or "restore" in result.output

    @patch("apple_device_cli.cli.list_signed_versions")
    @patch("apple_device_cli.cli.get_product_type_for_udid")
    def test_list_versions_prints_signed_ipsw_urls(
        self, mock_product_type, mock_list
    ):
        from apple_device_cli.restore.engine import SignedVersion
        mock_product_type.return_value = "iPad13,4"
        mock_list.return_value = [
            SignedVersion(
                version="26.6",
                build="23G71",
                url="https://example.com/iPad_26.6_23G71_Restore.ipsw",
                device="iPad13,4",
            ),
        ]
        result = runner.invoke(
            app, ["device", "restore", "--udid", "UDID-1", "--list-versions"]
        )
        # Exit 0 + the URL appears in stdout
        assert result.exit_code == 0
        assert "26.6" in result.output
        assert "23G71" in result.output

    @patch("apple_device_cli.cli.list_signed_versions")
    @patch("apple_device_cli.cli.get_product_type_for_udid")
    def test_list_versions_json_output(self, mock_product_type, mock_list):
        """--list-versions --json emits raw per-version objects for scripts."""
        from apple_device_cli.restore.engine import SignedVersion

        mock_product_type.return_value = "iPad13,4"
        mock_list.return_value = [
            SignedVersion(
                version="26.6",
                build="23G71",
                url="https://example.com/iPad_26.6_23G71_Restore.ipsw",
                device="iPad13,4",
            ),
        ]
        result = runner.invoke(
            app,
            [
                "device", "restore",
                "--udid", "UDID-1",
                "--list-versions", "--json",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert len(output) == 1
        assert output[0]["version"] == "26.6"
        assert output[0]["build"] == "23G71"
        assert output[0]["url"] == "https://example.com/iPad_26.6_23G71_Restore.ipsw"
        assert output[0]["device"] == "iPad13,4"
        assert output[0]["display_label"] == "iOS 26.6 (23G71)"

    @patch("apple_device_cli.cli.cache_state")
    def test_show_cache_prints_size_and_count(self, mock_state, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("IOS_ENROLL_CACHE_DIR", raising=False)
        # Wipe any existing config that might shadow the default
        cfg = Path.home() / ".config" / "ios-enroll" / "config.json"
        if cfg.exists():
            cfg.unlink()

        mock_state.return_value = {
            "path": str(tmp_path / "cache"),
            "size_bytes": 1_000_000_000,
            "ipsw_count": 2,
            "ipsw_files": ["a.ipsw", "b.ipsw"],
        }
        result = runner.invoke(app, ["device", "restore", "--show-cache"])
        assert result.exit_code == 0
        assert "1,000,000,000" in result.output or "1000000000" in result.output
        assert "a.ipsw" in result.output

    @patch("apple_device_cli.cli.cache_state")
    def test_show_cache_json_output(self, mock_state, tmp_path, monkeypatch):
        """--show-cache --json emits machine-readable cache state."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("IOS_ENROLL_CACHE_DIR", raising=False)
        # Wipe any existing config that might shadow the default
        cfg = Path.home() / ".config" / "ios-enroll" / "config.json"
        if cfg.exists():
            cfg.unlink()

        mock_state.return_value = {
            "path": str(tmp_path / "cache"),
            "size_bytes": 1234,
            "ipsw_count": 1,
            "ipsw_files": ["iPad_26.6_23G71_Restore.ipsw"],
        }
        result = runner.invoke(
            app, ["device", "restore", "--show-cache", "--json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["size_bytes"] == 1234
        assert data["ipsw_count"] == 1
        assert data["ipsw_files"] == ["iPad_26.6_23G71_Restore.ipsw"]
        assert data["path"] == str(tmp_path / "cache")

    @patch("apple_device_cli.cli.restore_device")
    @patch("apple_device_cli.cli.shutil.which")
    def test_restore_cli_accepts_ecid_option(
        self, mock_which, mock_restore, tmp_path
    ):
        from types import SimpleNamespace

        mock_which.return_value = "/usr/bin/idevicerestore"
        ipsw = tmp_path / "iPad_26.6_23G71_Restore.ipsw"
        ipsw.write_bytes(b"fake")
        mock_restore.return_value = SimpleNamespace(success=True, error=None, udid=None)

        result = runner.invoke(
            app,
            [
                "device", "restore",
                "--ecid", "0x00094daa01d80032",
                "--ipsw", str(ipsw),
            ],
        )

        assert result.exit_code == 0
        mock_restore.assert_called_once()
        kwargs = mock_restore.call_args.kwargs
        assert kwargs.get("ecid") == "0x00094daa01d80032"
        assert kwargs.get("udid") is None

    def test_restore_cli_rejects_ecid_and_udid_together(self):
        result = runner.invoke(
            app,
            [
                "device", "restore",
                "--ecid", "0x00094daa01d80032",
                "--udid", "UDID-1",
                "--ipsw", "x.ipsw",
            ],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output
