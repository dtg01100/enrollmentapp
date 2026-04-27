"""Device restore and update using pymobiledevice3."""
from __future__ import annotations

import subprocess
from pathlib import Path

from apple_device_cli.core.exceptions import RestoreError, ToolNotFoundError


def erase_device(udid: str) -> bool:
    """Erase device using pymobiledevice3."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pymobiledevice3", "restore", "update", "--erase", "--udid", udid],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            return True
        raise RestoreError(f"Erase failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RestoreError("Erase timed out after 600 seconds")
    except Exception as e:
        raise RestoreError(f"Erase failed: {e}")


def update_device(udid: str) -> bool:
    """Update device to latest iOS."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pymobiledevice3", "restore", "update", "--udid", udid],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            return True
        raise RestoreError(f"Update failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RestoreError("Update timed out after 600 seconds")
    except Exception as e:
        raise RestoreError(f"Update failed: {e}")


def restore_device(udid: str, ipsw: str | Path) -> bool:
    """Restore device with specific IPSW."""
    try:
        result = subprocess.run(
            ["python3", "-m", "pymobiledevice3", "restore", "--udid", udid, str(ipsw)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            return True
        raise RestoreError(f"Restore failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RestoreError("Restore timed out after 600 seconds")
    except Exception as e:
        raise RestoreError(f"Restore failed: {e}")