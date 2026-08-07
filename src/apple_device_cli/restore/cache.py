"""Cache-directory resolution and management for the restore engine.

The firmware cache holds 4-7 GB IPSW files; it must live on a drive
with enough free space. The default (``~/.cache/ios-enroll/firmware/``)
is on the user's home SSD, which fills fast. The CLI and GUI both
expose an override that wins.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from apple_device_cli.restore.errors import RestoreEngineError


ENV_VAR = "IOS_ENROLL_CACHE_DIR"


def _config_dir() -> Path:
    """Lazy: ``~/.config/ios-enroll/``. Computed at call time so tests
    that monkey-patch HOME before the first call see the patched path.
    """
    return Path.home() / ".config" / "ios-enroll"


def _config_file() -> Path:
    """Lazy: ``~/.config/ios-enroll/config.json``."""
    return _config_dir() / "config.json"


def _default_cache_dir() -> Path:
    """XDG-aware default cache dir.

    Uses ``$XDG_CACHE_HOME/ios-enroll/firmware/`` if XDG_CACHE_HOME
    is set, otherwise ``~/.cache/ios-enroll/firmware/``.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "ios-enroll" / "firmware"


def resolve_cache_dir(override: str | None = None) -> Path:
    """Resolve the firmware cache directory using the 4-tier precedence.

    Precedence (highest to lowest):
      1. ``override`` kwarg
      2. ``$IOS_ENROLL_CACHE_DIR`` env var
      3. ``~/.config/ios-enroll/config.json`` field ``cache_dir``
      4. ``$XDG_CACHE_HOME/ios-enroll/firmware/`` (or
         ``~/.cache/ios-enroll/firmware/`` if XDG_CACHE_HOME is unset)

    All paths must be absolute — relative paths raise RestoreEngineError
    because a config file in the working dir is too easy to misread.

    The result is created (parents=True, exist_ok=True) before return.
    """
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    env_val = os.environ.get(ENV_VAR)
    if env_val:
        candidates.append(Path(env_val))
    if _config_file().exists():
        try:
            data = json.loads(_config_file().read_text())
        except json.JSONDecodeError as exc:
            raise RestoreEngineError(
                f"Could not parse {_config_file()}: {exc}. "
                f"Fix or delete the file, or pass --cache-dir."
            ) from exc
        cfg_val = data.get("cache_dir")
        if cfg_val:
            candidates.append(Path(cfg_val))
    candidates.append(_default_cache_dir())

    for path in candidates:
        if not path.is_absolute():
            raise RestoreEngineError(
                f"Cache directory must be an absolute path, got: {path}. "
                f"Set --cache-dir, $IOS_ENROLL_CACHE_DIR, or "
                f"the cache_dir field in {_config_file()} to an absolute path."
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    # Unreachable — _default_cache_dir() always returns a valid absolute path.
    raise RestoreEngineError("No cache directory could be resolved.")


def cache_state(cache_dir: Path) -> dict:
    """Return a dict describing the current cache contents.

    Keys: ``path`` (str), ``size_bytes`` (int), ``ipsw_count`` (int),
    ``ipsw_files`` (list[str], basenames only).
    """
    if not cache_dir.exists():
        return {
            "path": str(cache_dir),
            "size_bytes": 0,
            "ipsw_count": 0,
            "ipsw_files": [],
        }
    files = [p for p in cache_dir.rglob("*.ipsw") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    return {
        "path": str(cache_dir),
        "size_bytes": size,
        "ipsw_count": len(files),
        "ipsw_files": [p.name for p in files],
    }


def write_cache_config(cache_dir: Path) -> None:
    """Persist ``cache_dir`` to the user config file.

    Used by the GUI's "Cache folder..." picker. Idempotent: reads
    existing JSON, updates ``cache_dir`` only, leaves other fields
    alone. Raises RestoreEngineError on permission / parse errors.
    """
    config_dir = _config_dir()
    config_file = _config_file()
    config_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
        except json.JSONDecodeError as exc:
            raise RestoreEngineError(
                f"Could not parse {config_file}: {exc}. "
                f"Delete the file or fix it manually."
            ) from exc
    data["cache_dir"] = str(cache_dir)
    config_file.write_text(json.dumps(data, indent=2) + "\n")
