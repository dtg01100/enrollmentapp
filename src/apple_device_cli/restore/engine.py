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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


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
