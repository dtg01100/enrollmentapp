"""Tests for restore/cache.py — the cache-dir resolution chain.

Precedence (highest to lowest):
  1. ``override`` kwarg (CLI flag / GUI picker)
  2. ``$IOS_ENROLL_CACHE_DIR`` env var
  3. ``~/.config/ios-enroll/config.json`` field ``cache_dir``
  4. ``~/.cache/ios-enroll/firmware/`` (XDG default)
"""
from __future__ import annotations

import json

import pytest

from apple_device_cli.restore import cache
from apple_device_cli.restore.errors import RestoreEngineError


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() and XDG_CACHE_HOME to a tmp dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows: Path.home() checks USERPROFILE first
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.delenv("IOS_ENROLL_CACHE_DIR", raising=False)
    return home


def test_no_override_uses_xdg_default(tmp_path, monkeypatch, fake_home):
    """With no override, env, or config — return the XDG default."""
    monkeypatch.chdir(tmp_path)
    result = cache.resolve_cache_dir()
    assert result == fake_home / ".cache" / "ios-enroll" / "firmware"
    assert result.exists()  # mkdir happens
    assert result.is_dir()


def test_cli_flag_wins_over_everything(tmp_path, monkeypatch, fake_home):
    """The override kwarg beats env and config."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IOS_ENROLL_CACHE_DIR", str(tmp_path / "from_env"))
    config_file = fake_home / ".config" / "ios-enroll" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"cache_dir": str(tmp_path / "from_config")}))

    override = tmp_path / "from_cli"
    result = cache.resolve_cache_dir(override=str(override))
    assert result == override


def test_env_var_wins_over_config(tmp_path, monkeypatch, fake_home):
    """$IOS_ENROLL_CACHE_DIR beats the config file."""
    monkeypatch.chdir(tmp_path)
    config_file = fake_home / ".config" / "ios-enroll" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"cache_dir": str(tmp_path / "from_config")}))

    env_path = tmp_path / "from_env"
    monkeypatch.setenv("IOS_ENROLL_CACHE_DIR", str(env_path))
    result = cache.resolve_cache_dir()
    assert result == env_path


def test_config_file_wins_over_default(tmp_path, monkeypatch, fake_home):
    """~/.config/ios-enroll/config.json beats the XDG default."""
    monkeypatch.chdir(tmp_path)
    cfg_path = tmp_path / "from_config"
    config_file = fake_home / ".config" / "ios-enroll" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"cache_dir": str(cfg_path)}))

    result = cache.resolve_cache_dir()
    assert result == cfg_path


def test_missing_config_file_falls_through_to_default(
    tmp_path, monkeypatch, fake_home
):
    """A non-existent config file is treated as 'no override'."""
    monkeypatch.chdir(tmp_path)
    result = cache.resolve_cache_dir()
    assert result == fake_home / ".cache" / "ios-enroll" / "firmware"


def test_relative_path_in_config_is_rejected(tmp_path, monkeypatch, fake_home):
    """A relative cache_dir in config.json raises — caller passes an absolute path."""
    monkeypatch.chdir(tmp_path)
    config_file = fake_home / ".config" / "ios-enroll" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"cache_dir": "relative/path"}))

    with pytest.raises(RestoreEngineError, match="absolute"):
        cache.resolve_cache_dir()


def test_relative_env_var_is_rejected(tmp_path, monkeypatch, fake_home):
    """Same rule for the env var."""
    monkeypatch.setenv("IOS_ENROLL_CACHE_DIR", "relative/path")
    with pytest.raises(RestoreEngineError, match="absolute"):
        cache.resolve_cache_dir()
