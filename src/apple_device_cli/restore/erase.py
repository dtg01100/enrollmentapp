"""Device restore and update using pymobiledevice3."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from apple_device_cli.core.exceptions import RestoreError

try:
    from pymobiledevice3.irecv import IRecv
except ImportError:  # not available without the package installed
    IRecv = None  # type: ignore[assignment,misc]

try:
    from pymobiledevice3.restore.device import Device
except ImportError:
    Device = None  # type: ignore[assignment,misc]

try:
    from pymobiledevice3.restore.restore import Restore
except ImportError:
    Restore = None  # type: ignore[assignment,misc]

try:
    from pymobiledevice3.restore.base_restore import Behavior
except ImportError:
    Behavior = None  # type: ignore[assignment,misc]

try:
    from ipsw_parser.ipsw import IPSW
except ImportError:
    IPSW = None  # type: ignore[assignment,misc]

IPSW_API_URL = "https://api.ipsw.me/v4/device/{identifier}?type=ipsw"

# How long (seconds) to retry transient failures when waiting for the iPad.
_DEVICE_WAIT_TIMEOUT = 120
# Pause between retries.
_RETRY_INTERVAL = 2


def _check_disk_space(path: Path, required_bytes: int) -> tuple[bool, int]:
    """Check if there's enough disk space at the given path.

    Args:
        path: Path to check (directory or file location)
        required_bytes: Minimum bytes needed

    Returns:
        Tuple of (has_space, available_bytes)
    """
    try:
        stat = os.statvfs(path.parent if path.is_file() else path)
        available = stat.f_bavail * stat.f_frsize
        return available >= required_bytes, available
    except OSError:
        return True, 0


def _get_work_dir(work_dir: str | Path | None = None) -> Path:
    """Get working directory for IPSW operations.

    Args:
        work_dir: Explicit work directory, or None to use cwd.

    Returns:
        Path to working directory
    """
    if work_dir:
        return Path(work_dir)
    return Path.cwd()


class InsufficientSpaceError(RestoreError):
    """Raised when there's not enough disk space for IPSW operations."""

    def __init__(self, needed_mb: int, available_mb: int, target_dir: Path):
        self.needed_mb = needed_mb
        self.available_mb = available_mb
        self.target_dir = target_dir
        super().__init__(
            f"Not enough disk space at '{target_dir}'. "
            f"Need {needed_mb} MB, but only {available_mb} MB available. "
            f"Use --work-dir to specify a location with more space."
        )


def _download_ipsw(url: str, timeout: int = 600, work_dir: str | Path | None = None, progress_callback: Callable[[str], None] | None = None) -> Path:
    """Download an IPSW file from a URL to a working directory.

    Args:
        url: URL to the IPSW file
        timeout: Download timeout in seconds (default 10 min)
        work_dir: Directory to store IPSW (default: cwd)
        progress_callback: Optional callback for progress messages

    Returns:
        Path to the downloaded IPSW file

    Raises:
        RestoreError: If download fails
        InsufficientSpaceError: If not enough disk space
    """
    import logging

    filename = url.split("/")[-1]
    target_dir = _get_work_dir(work_dir)
    tmp_path = target_dir / filename

    def _progress(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        else:
            logging.info(msg)

    try:
        with urlopen(url, timeout=timeout) as response:
            total_size = int(response.headers.get("Content-Length", 0))

            if total_size > 0:
                min_required = int(total_size * 1.1)
                has_space, available = _check_disk_space(tmp_path, min_required)
                if not has_space:
                    available_mb = available // (1024**2)
                    needed_mb = min_required // (1024**2)
                    raise InsufficientSpaceError(
                        needed_mb,
                        available_mb,
                        target_dir,
                    )
                _progress(f"IPSW size: {total_size // (1024**2)} MB")

            if tmp_path.exists() and tmp_path.stat().st_size == total_size:
                _progress(f"Using cached IPSW: {tmp_path}")
                return tmp_path

            _progress(f"Downloading IPSW ({filename})...")

            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB chunks

            with open(tmp_path, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        _progress(f"  Downloaded {downloaded // (1024*1024)}/{total_size // (1024**2)} MB ({pct:.0f}%)")

            _progress(f"Download complete: {tmp_path}")
            return tmp_path
    except InsufficientSpaceError:
        raise
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RestoreError(f"Failed to download IPSW from {url}: {e}") from e


def _ensure_ipsw_local(ipsw: str | Path, work_dir: str | Path | None = None) -> Path:
    """Ensure IPSW path is a local file, downloading if necessary.

    Args:
        ipsw: IPSW path (local file or URL)
        work_dir: Directory to store downloaded IPSW (default: cwd)

    Returns:
        Path to local IPSW file

    Raises:
        RestoreError: If download fails or file doesn't exist
    """
    ipsw_str = str(ipsw)

    if ipsw_str.startswith(("http://", "https://")):
        return _download_ipsw(ipsw_str, work_dir=work_dir)

    local_path = Path(ipsw_str)
    if not local_path.exists():
        raise RestoreError(f"IPSW file not found: {local_path}")
    return local_path


def get_irecv():
    """Return a live IRecv handle (used for Recovery/DFU device communication)."""
    if IRecv is None:
        raise RestoreError("pymobiledevice3.irecv not available")
    return IRecv(timeout=5, is_recovery=True)


def _retry_until(fn, *, timeout: int = _DEVICE_WAIT_TIMEOUT, interval: float = _RETRY_INTERVAL, label: str = "operation"):
    """Call *fn()* repeatedly until it returns without raising, or *timeout* expires.

    Returns whatever *fn()* returns on success.
    Raises RestoreError with the last exception message if the deadline is hit.
    """
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            time.sleep(interval)
    raise RestoreError(f"{label} did not succeed within {timeout}s: {last_exc}")


def _usbmuxd_socket_path() -> str:
    try:
        from pymobiledevice3.common import get_os_utils
        path, _ = get_os_utils().usbmux_address
        return path
    except Exception:
        return "/var/run/usbmuxd"


def _connect_usbmuxd() -> None:
    """Raise OSError if the usbmuxd socket is not yet connectable."""
    import socket as _socket
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(_usbmuxd_socket_path())
    finally:
        s.close()


def get_signed_firmwares(identifier: str) -> list[dict[str, str]]:
    """Fetch signed IPSW builds for a device identifier from ipsw.me."""
    url = IPSW_API_URL.format(identifier=identifier)
    try:
        with urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as e:
        raise RestoreError(f"Failed to fetch signed iOS versions for {identifier}: {e}") from e

    firmwares = payload.get("firmwares", [])
    signed = [
        {
            "version": str(entry.get("version", "Unknown")),
            "buildid": str(entry.get("buildid", "Unknown")),
            "url": str(entry.get("url", "")),
        }
        for entry in firmwares
        if entry.get("signed") and entry.get("url")
    ]
    if not signed:
        raise RestoreError(f"No signed iOS versions found for {identifier}")
    return signed


def resolve_firmware_url(identifier: str, version_or_build: str) -> str:
    """Resolve a signed firmware URL from a version number or build id."""
    needle = version_or_build.strip().lower()
    firmwares = get_signed_firmwares(identifier)
    if needle == "latest":
        return firmwares[0]["url"]
    for firmware in firmwares:
        if firmware["version"].lower() == needle or firmware["buildid"].lower() == needle:
            return firmware["url"]
    raise RestoreError(f"No signed build matching '{version_or_build}' was found for {identifier}")


def enter_recovery_mode(udid: str, ecid: str | None = None, timeout: int = 180) -> bool:
    """Place a device into Recovery mode and wait until it appears there.

    1. Sends the enter-recovery command via the lockdown Python API (returns
       immediately after the USB control transfer).
    2. Then blocks — using IRecv — until the device re-appears in Recovery /
       DFU mode.  This can take 30-90 s depending on the device.

    Args:
        udid:    Device UDID (used to connect lockdown).
        ecid:    Device ECID hex string (e.g. '0xe28e921780032').
                 When supplied, IRecv will wait specifically for *this* device.
        timeout: Seconds to wait for the device to appear in Recovery (default 180 s).
    """
    # Step 1: send the recovery command (requires the device to currently be in
    # normal mode and usbmuxd to be up).
    def _send_recovery_cmd() -> None:
        from pymobiledevice3.lockdown import create_using_usbmux

        async def _enter() -> None:
            lockdown = await create_using_usbmux(serial=udid)
            await lockdown.enter_recovery()

        asyncio.run(_enter())

    _retry_until(_send_recovery_cmd, timeout=30, interval=2, label=f"send recovery command to {udid}")

    # Step 2: wait for the device to boot into Recovery/DFU.  The device
    # disappears from the USB bus while rebooting (errno 5 / IOError) so we
    # retry each short IRecv probe until one succeeds.
    _IRecv = IRecv
    if _IRecv is None:
        raise RestoreError("pymobiledevice3.irecv not available")

    ecid_int = int(ecid, 16) if ecid else None

    def _probe_recovery() -> None:
        irecv = _IRecv(ecid=ecid_int, timeout=2, is_recovery=True)
        if irecv._device is not None:
            try:
                irecv._device.reset()
            except Exception:
                pass

    _retry_until(_probe_recovery, timeout=timeout, interval=2, label=f"device {udid} entering Recovery mode")

    # Device is now confirmed in Recovery mode (libusb/IRecv path).
    # usbmuxd is not involved; Restore.update() connects via libusb.
    return True


def _restore_with_api(
    ecid_int: int,
    ipsw_path: str | Path,
    behavior: Behavior,
    work_dir: str | Path | None = None,
) -> None:
    """Perform a restore operation using the pymobiledevice3 Python API.

    Args:
        ecid_int:    Device ECID as an integer.
        ipsw_path:   Path to the IPSW file (local path or URL).
        behavior:    Restore behavior (Update, Erase, etc.).
        work_dir:    Directory for IPSW storage (default: cwd).

    Raises:
        RestoreError: If any step fails.
    """
    if IPSW is None:
        raise RestoreError("ipsw_parser not available")
    if Restore is None or Device is None or Behavior is None:
        raise RestoreError("pymobiledevice3 restore components not available")

    target_dir = _get_work_dir(work_dir)
    local_ipsw = _ensure_ipsw_local(ipsw_path, work_dir=work_dir)
    ipsw_size = local_ipsw.stat().st_size
    min_required = int(ipsw_size * 1.2)
    has_space, available = _check_disk_space(local_ipsw, min_required)
    if not has_space:
        available_mb = available // (1024**2)
        needed_mb = min_required // (1024**2)
        raise InsufficientSpaceError(needed_mb, available_mb, target_dir)
    irecv = IRecv(ecid=ecid_int, timeout=5, is_recovery=True)
    ipsw = IPSW.create_from_path(str(local_ipsw))
    device = Device(irecv=irecv)

    async def _do_restore() -> None:
        restore = Restore(ipsw=ipsw, device=device, behavior=behavior)
        await restore.update()

    asyncio.run(_do_restore())


def erase_device(udid: str, ecid: str | None = None, ipsw: str | Path | None = None, work_dir: str | Path | None = None) -> bool:
    """Erase device using pymobiledevice3.

    Args:
        udid: Device UDID (used for identification/logging).
        ecid: Device ECID hex string (e.g. '0xe28e921780032').
        ipsw: IPSW path (required).
        work_dir: Directory for IPSW storage (default: cwd).

    Returns:
        True if erase succeeded.

    Raises:
        RestoreError: If erase fails or times out.
    """
    if ecid is None:
        raise RestoreError("erase_device requires an ECID")
    if ipsw is None:
        raise RestoreError("erase_device requires an IPSW path")
    ecid_int = int(ecid, 16)
    target = f"Erase for device {udid}"
    try:
        _restore_with_api(ecid_int, ipsw, Behavior.Erase, work_dir=work_dir)
        return True
    except Exception as e:
        raise RestoreError(f"{target} failed: {e}") from e


def update_device(udid: str, ecid: str | None = None, ipsw: str | Path | None = None, work_dir: str | Path | None = None) -> bool:
    """Update device to latest iOS.

    Args:
        udid: Device UDID (used for identification/logging).
        ecid: Device ECID hex string (e.g. '0xe28e921780032').
        ipsw: IPSW path (required).
        work_dir: Directory for IPSW storage (default: cwd).

    Returns:
        True if update succeeded.

    Raises:
        RestoreError: If update fails or times out.
    """
    if ecid is None:
        raise RestoreError("update_device requires an ECID")
    if ipsw is None:
        raise RestoreError("update_device requires an IPSW path")
    ecid_int = int(ecid, 16)
    target = f"Update for device {udid}"
    try:
        _restore_with_api(ecid_int, ipsw, Behavior.Update, work_dir=work_dir)
        return True
    except Exception as e:
        raise RestoreError(f"{target} failed: {e}") from e


def restore_device(udid: str, ipsw: str | Path, ecid: str | None = None, work_dir: str | Path | None = None) -> bool:
    """Restore device with specific IPSW.

    Args:
        udid: Device UDID (used for identification/logging).
        ipsw: Path to IPSW file.
        ecid: Device ECID hex string (e.g. '0xe28e921780032').
        work_dir: Directory for IPSW storage (default: cwd).

    Returns:
        True if restore succeeded.

    Raises:
        RestoreError: If restore fails or times out.
    """
    if ecid is None:
        raise RestoreError("restore_device requires an ECID")
    ecid_int = int(ecid, 16)
    target = f"Restore for device {udid}"
    try:
        _restore_with_api(ecid_int, ipsw, Behavior.Erase, work_dir=work_dir)
        return True
    except Exception as e:
        raise RestoreError(f"{target} failed: {e}") from e
