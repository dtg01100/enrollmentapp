"""Restore engine: wraps idevicerestore + pymobiledevice3 for the
supervised Restore flow and the GUI Restore tab.

Public surface (see ios-enroll-restore-tab spec):
  - ``SignedVersion``: frozen dataclass for one signed iOS restore image
  - ``RestoreResult``: dataclass for the outcome of a restore
  - ``parse_ipsw_url``: pure parser for Apple CDN URLs (no subprocess)
  - ``list_signed_versions(product_type)``: subprocess wrapper
  - ``download_ipsw(url, dest_dir)``: urllib wrapper with resume
  - ``get_product_type_for_udid(udid)``: lockdown lookup
  - ``restore_device(...)``: idevicerestore wrapper
  - ``restore_device_via_pymd3(...)``: pymobiledevice3 fallback

All subprocess calls use ``Popen`` with ``stdin=DEVNULL`` and no
timeout — older iPads can run 45-60+ minutes for a full restore.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SignedVersion:
    """One signed iOS restore image, as returned by ``list_signed_versions``."""
    version: str          # e.g. "26.6"
    build: str            # e.g. "23G71"
    url: str              # full Apple CDN URL
    device: str           # ProductType, e.g. "iPad13,4"

    @property
    def display_label(self) -> str:
        return f"iOS {self.version} ({self.build})"


@dataclass
class RestoreResult:
    success: bool
    udid: str | None = None
    ipsw_path: Path | None = None
    error: str | None = None
    log_excerpt: str = ""  # last ~50 lines of idevicerestore output


# Apple CDN URL pattern:
# .../<DeviceName>_<iOSVersion>_<Build>_Restore.ipsw
# iOSVersion: digits and dots, e.g. "26.6", "17.5.1", "18.0"
# Build: alphanumeric, e.g. "23G71", "21F90", "21A342"
_URL_RE = re.compile(
    r"^(?P<base>https?://[^\s]+?/)"
    r"(?P<device_name>[^/]+?)_"
    r"(?P<version>\d+(?:\.\d+){1,2})_"
    r"(?P<build>[A-Za-z0-9]+?)"
    r"_Restore\.ipsw$"
)


def parse_ipsw_url(url: str, device: str) -> SignedVersion | None:
    """Parse an Apple CDN URL into a SignedVersion, or return None if it
    doesn't match the expected pattern. The ``device`` argument is the
    ProductType (e.g. ``"iPad13,4"``) — the URL itself doesn't contain
    it, so the caller supplies it.
    """
    match = _URL_RE.match(url.strip())
    if not match:
        return None
    return SignedVersion(
        version=match.group("version"),
        build=match.group("build"),
        url=url.strip(),
        device=device,
    )


def _popen_capture(cmd: list[str], **kwargs):
    """Thin wrapper around ``subprocess.Popen`` so tests can patch it.

    Captures stdout via PIPE; ``stdin=DEVNULL`` so the child cannot
    prompt on a TTY. Text mode + line buffering for streaming.
    """
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.STDOUT)
    kwargs.setdefault("text", True)
    kwargs.setdefault("bufsize", 1)
    return subprocess.Popen(cmd, **kwargs)


def list_signed_versions(product_type: str) -> list[SignedVersion]:
    """List signed iOS restore images for a given ProductType.

    Runs ``ipsw download ipsw --device <product_type> --urls`` and
    parses the output (one URL per line, newest first). Returns a
    list of SignedVersion, or raises RestoreEngineError on missing
    binary, non-zero exit, or zero output.
    """
    from apple_device_cli.restore.errors import RestoreEngineError

    if not shutil.which("ipsw"):
        raise RestoreEngineError(
            "The 'ipsw' tool is required to list signed versions. "
            "Install with: brew install ipsw"
        )

    proc = _popen_capture(
        ["ipsw", "download", "ipsw", "--device", product_type, "--urls"]
    )
    raw_lines: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            raw_lines.append(line)
    proc.wait()
    raw = "".join(raw_lines)

    if proc.returncode != 0:
        raise RestoreEngineError(
            f"`ipsw` exited with code {proc.returncode} while listing signed "
            f"versions for {product_type}. Output:\n{raw}"
        )

    versions: list[SignedVersion] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = parse_ipsw_url(line, device=product_type)
        if parsed is not None:
            versions.append(parsed)

    if not versions:
        raise RestoreEngineError(
            f"`ipsw` returned no signed IPSW URLs for {product_type}. "
            f"This can mean the device is no longer supported or the "
            f"`ipsw` tool is out of date. Output was:\n{raw[:500]}"
        )

    return versions


MAX_DOWNLOAD_ATTEMPTS = 3
_DOWNLOAD_CHUNK = 64 * 1024


def _filename_from_url(url: str) -> str:
    """Extract the basename from an Apple CDN URL.

    Falls back to ``ipsw.partial`` if the URL has no recognizable
    filename (shouldn't happen for Apple URLs, but defensive).
    """
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail else "ipsw.partial"


def download_ipsw(url: str, dest_dir: Path) -> Path:
    """Stream the URL to ``dest_dir/<basename>`` with HTTP Range-resume.

    Behavior:
      - If ``dest_dir/<basename>`` exists, check size and use
        ``Range: bytes=<existing>-`` to resume. (The pre-existing
        file is left in place during download under a ``.partial``
        suffix and renamed on success.)
      - Writes to ``dest_dir/<basename>.partial`` during download
        and renames on success.
      - Up to ``MAX_DOWNLOAD_ATTEMPTS`` retries on network errors.
        After that, raises ``RestoreEngineError`` with the last
        urllib error attached.
    """
    from apple_device_cli.restore.errors import RestoreEngineError

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = _filename_from_url(url)
    final = dest_dir / filename
    partial = dest_dir / (filename + ".partial")

    last_exc: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        existing = partial.stat().st_size if partial.exists() else 0
        req = Request(url)
        if existing > 0:
            req.add_header("Range", f"bytes={existing}-")

        try:
            with urlopen(req, timeout=60) as resp:
                # If we asked for a Range but the server replied 200 (full
                # content from byte 0), the partial is stale — discard it
                # and start fresh. (urlopen raises HTTPError for 416; we
                # treat that as 'partial is at-or-past the end'.)
                if existing > 0 and resp.headers.get("Content-Range") is None:
                    # Server didn't honor the Range — full body coming
                    existing = 0
                mode = "ab" if existing > 0 else "wb"
                with open(partial, mode) as f:
                    while True:
                        chunk = resp.read(_DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                partial.replace(final)
                return final
        except (URLError, OSError) as exc:
            last_exc = exc
            if attempt < MAX_DOWNLOAD_ATTEMPTS:
                continue
            break

    raise RestoreEngineError(
        f"Failed to download {url} after {MAX_DOWNLOAD_ATTEMPTS} attempts. "
        f"Last error: {last_exc}. Check network and free disk space at "
        f"{dest_dir}."
    )


def _create_using_usbmux_with_pair_retry(serial: str):
    """Coroutine: connect to ``serial`` with pair-on-failure retry.

    Thin wrapper around the existing ``_pair_then_retry_connect``
    from ``apple_device_cli.enrollment.supervised``. Returns a
    coroutine that resolves to the lockdown. Kept here as a
    module-level indirection so tests can patch it.
    """
    from apple_device_cli.enrollment.supervised import (
        _pair_then_retry_connect,
    )
    from apple_device_cli.device.connection import (
        ensure_device_pairing,
        wait_for_udid_in_usbmux,
    )
    from pymobiledevice3.lockdown import create_using_usbmux

    async def _connect():
        return await _pair_then_retry_connect(
            udid=serial,
            connect=create_using_usbmux,
            ensure_pairing=ensure_device_pairing,
            wait_for_udid=wait_for_udid_in_usbmux,
        )
    return _connect()


async def get_product_type_for_udid(udid: str) -> str:
    """Connect to ``udid`` (with iOS 26 pair-on-failure) and return
    lockdown.ProductType.

    Raises ``RestoreEngineError`` if the device is in recovery/DFU
    (no Normal-mode lockdown), or if ProductType is missing.
    """
    from apple_device_cli.restore.errors import RestoreEngineError

    try:
        lockdown = await _create_using_usbmux_with_pair_retry(udid)
    except ConnectionError as exc:
        raise RestoreEngineError(
            f"Could not connect to device {udid}. If the device is in "
            f"recovery or DFU mode, exit recovery (or use the "
            f"ios-device-recovery skill for stuck devices) and plug it "
            f"in normally. Underlying error: {exc}"
        ) from exc

    raw = await lockdown.get_value(None, "ProductType")
    if isinstance(raw, dict) and "Value" in raw:
        product_type = raw["Value"]
    else:
        product_type = raw
    if not product_type:
        raise RestoreEngineError(
            f"Device {udid} returned no ProductType from lockdown. "
            f"The device may be in an unusual state — check "
            f"`ideviceinfo -u {udid}`."
        )
    return str(product_type)
