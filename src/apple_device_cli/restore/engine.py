"""Restore engine: wraps idevicerestore + pymobiledevice3 for the
supervised Restore flow and the GUI Restore tab.

Public surface (see ios-enroll-restore-tab spec):
  - ``SignedVersion``: frozen dataclass for one signed iOS restore image
  - ``RestoreResult``: dataclass for the outcome of a restore
  - ``ProgressUpdate``: one parsed progress event (step name or percent)
  - ``ProgressEvent``: one output line plus an optional ``ProgressUpdate``
  - ``parse_ipsw_url``: pure parser for Apple CDN URLs (no subprocess)
  - ``parse_progress_line``: pure parser for idevicerestore progress lines
   - ``list_signed_versions(product_type)``: subprocess wrapper
   - ``download_ipsw(url, dest_dir, progress_callback=None)``: urllib wrapper
     with resume, cache-hit short-circuit, and streaming progress
  - ``get_product_type_for_udid(udid)``: lockdown lookup
  - ``detect_device_mode(udid)``: USB product-ID lookup (normal/recovery/restore/dfu)
  - ``detect_recovery_devices_present()``: True if any iBoot-mode USB device is on the bus
   - ``enter_recovery_mode(udid)``: lockdownd EnterRecovery request
   - ``exit_recovery_mode(udid=None)``: ``irecovery --normal`` out of recovery
     (no UDID needed)
   - ``recovery_device_descriptor()``: ``(SRNM, ECID)`` of the first
     Recovery-mode USB device, or None (feeds the GUI's synthetic
     device-combo entry)
    - ``restore_device(...)``: idevicerestore wrapper (mode-aware: ``-u`` in
      Normal mode, ``-i <ecid>`` for Recovery/restore/DFU-mode devices)
    - ``restore_device_via_pymd3(...)``: pymobiledevice3 fallback

All subprocess calls use ``Popen`` with ``stdin=DEVNULL`` and no
timeout — older iPads can run 45-60+ minutes for a full restore.

Progress: ``restore_device`` runs ``idevicerestore -P`` (plain
progress) and every output line is delivered to the callback as a
``ProgressEvent``. ``parse_progress_line`` understands both the
plain format (``PROGRESS: 12/30`` / ``STEP: Restoring Baseband``)
and the default format (``Uploading [====...] 49.7%``), so the bar
keeps working even if ``idevicerestore`` ever drops ``-P``.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal
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


@dataclass(frozen=True)
class ProgressUpdate:
    """One parsed progress event from ``idevicerestore`` output.

    ``kind == "step"`` carries the current step's name in ``label``
    (from the plain ``-P`` output: ``STEP: Restoring Baseband``).
    ``kind == "percent"`` carries ``value`` (already computed to a
    0-100 int by the parser) and ``total`` (the raw denominator,
    preserved so the UI can render ``12/30`` if it wants to).
    """
    kind: Literal["step", "percent"]
    value: int | None
    total: int | None
    label: str | None


@dataclass(frozen=True)
class ProgressEvent:
    """One line of ``idevicerestore`` output delivered to the callback.

    ``text`` is the raw line (newline stripped) for the log scrollback;
    ``progress`` is the parsed ``ProgressUpdate``, or ``None`` when the
    line carries no progress information.
    """
    text: str
    progress: ProgressUpdate | None = None


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


_PLAIN_PROGRESS_RE = re.compile(
    r"^PROGRESS:\s*(\d+)\s*/\s*(\d+)\s*$", re.IGNORECASE
)
# A STEP: line that carries its own trailing percentage, e.g.
# ``STEP: Restoring Baseband 45%``. Parsed as a percent event so the bar
# moves even when the plain format never emits PROGRESS: lines.
_PLAIN_STEP_PERCENT_RE = re.compile(
    r"^STEP:\s*(.+?)\s+(\d{1,3}(?:\.\d+)?)\s*%\s*$", re.IGNORECASE
)
_PLAIN_STEP_RE = re.compile(r"^STEP:\s*(.+?)\s*$", re.IGNORECASE)
_UPLOADING_RE = re.compile(
    r"^\s*Uploading\s+\[.*\]\s*(\d+(?:\.\d+)?)\s*%\s*$", re.IGNORECASE
)
# idevicerestore -P emits ``Uploading:   0.5`` (colon + decimal fraction
# 0.0-1.0), not the bracket-bar form the default format uses.
_UPLOADING_COLON_RE = re.compile(
    r"^\s*Uploading:\s+(\d+\.\d+)\s*$", re.IGNORECASE
)


def parse_progress_line(line: str) -> ProgressUpdate | None:
    """Parse one ``idevicerestore`` line into a ``ProgressUpdate``.

    Handles both output formats:

    - Plain (``-P, --plain-progress``): ``PROGRESS: 12/30`` and
      ``STEP: Restoring Baseband``. A ``STEP:`` line that carries its own
      trailing percentage (``STEP: Restoring Baseband 45%``) is parsed as a
      percent event (value 45, total 100) with the step name kept as the
      label, so the bar moves even when ``PROGRESS:`` lines are sparse.
      ``Uploading:   0.5`` (colon + decimal fraction 0.0-1.0) is parsed as a
      percent event too (value 50, total 100).
    - Default: ``Uploading [====...] 49.7%``. This parser is
      stateless, so the step label from the preceding ``Sending`` /
      ``Personalizing`` header lines is NOT recovered here — the
      caller (GUI) tracks the current step name instead.

    Returns ``None`` for lines that carry no progress information
    (they still flow to the log scrollback as ``ProgressEvent`` with
    ``progress=None``).
    """
    stripped = line.strip()
    if not stripped:
        return None

    match = _PLAIN_PROGRESS_RE.match(stripped)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        value = int(round((current / total) * 100)) if total else 0
        return ProgressUpdate(kind="percent", value=value, total=total, label=None)

    match = _PLAIN_STEP_PERCENT_RE.match(stripped)
    if match:
        value = int(round(float(match.group(2))))
        return ProgressUpdate(
            kind="percent", value=value, total=100, label=match.group(1).strip()
        )

    match = _PLAIN_STEP_RE.match(stripped)
    if match:
        return ProgressUpdate(kind="step", value=None, total=None, label=match.group(1).strip())

    match = _UPLOADING_RE.match(stripped)
    if match:
        value = int(round(float(match.group(1))))
        return ProgressUpdate(kind="percent", value=value, total=100, label=None)

    match = _UPLOADING_COLON_RE.match(stripped)
    if match:
        fraction = float(match.group(1))
        value = int(round(fraction * 100))
        return ProgressUpdate(kind="percent", value=value, total=100, label=None)

    return None


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


# Apple CDN IPSW filename pattern: <Device>_<Version>_<Build>_Restore.ipsw
# e.g. iPad15,7_26.6_23G71_Restore.ipsw
_FILENAME_RE = re.compile(
    r"^(?P<device>[^_]+)_(?P<version>\d+(?:\.\d+){1,2})_"
    r"(?P<build>[A-Za-z0-9]+)_Restore\.ipsw$"
)


def parse_ipsw_filename(name: str) -> tuple[str, str, str] | None:
    """Parse ``iPad15,7_26.6_23G71_Restore.ipsw`` → (device, version, build).

    Returns None when the name doesn't match the Apple CDN pattern.
    """
    m = _FILENAME_RE.match(name.strip())
    if m is None:
        return None
    return m.group("device"), m.group("version"), m.group("build")


def cached_ipsw_path(
    url_or_name: str, cache_dir: Path | None = None
) -> Path | None:
    """Return the Path of a cached IPSW matching ``url_or_name``'s basename,
    or None when absent or zero-size. Does not verify integrity.

    ``url_or_name`` may be a full Apple CDN URL or a bare filename.
    """
    from apple_device_cli.restore.cache import resolve_cache_dir

    cache = cache_dir or resolve_cache_dir()
    if not cache or not cache.is_dir():
        return None
    name = _filename_from_url(url_or_name)
    candidate = cache / name
    if candidate.is_file() and candidate.stat().st_size > 0:
        return candidate
    return None


_HASH_CHUNK = 1024 * 1024


def hash_ipsw(path: Path, algorithm: str = "sha1") -> str:
    """Stream-hash ``path`` (``sha1`` / ``sha256`` / ``md5``).

    Returns lowercase hex. Chunked 1 MiB reads so a ~10 GB IPSW doesn't
    blow memory. Raises FileNotFoundError / OSError on I/O problems.
    """
    import hashlib

    h = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class FirmwareIdentity:
    """Expected identity of one firmware as published by ipsw.me."""

    version: str
    build: str
    device: str          # ProductType, e.g. "iPad15,7"
    url: str
    sha1sum: str         # lowercase hex or ""
    sha256sum: str       # lowercase hex or ""
    md5sum: str          # lowercase hex or ""
    filesize: int | None


_IPSW_ME_TIMEOUT = 10


def lookup_ipsw_me(
    device: str,
    build: str | None = None,
    version: str | None = None,
) -> FirmwareIdentity | None:
    """Look up a firmware's published hashes on ipsw.me (on demand).

    GETs ``https://api.ipsw.me/v4/device/{device}?type=ipsw`` and returns
    the matching firmware — preferred match by ``buildid``, falling back
    to ``version`` when ``build`` is None. Returns None when the device
    isn't listed, no entry matches, or the network call fails. Never
    raises: callers report a failed lookup via ``expected=None``.
    """
    import json
    from urllib.request import Request as _Request

    url = f"https://api.ipsw.me/v4/device/{device}?type=ipsw"
    try:
        req = _Request(url, headers={"User-Agent": "ios-enroll/1.3"})
        with urlopen(req, timeout=_IPSW_ME_TIMEOUT) as resp:
            payload = json.load(resp)
    except Exception:  # noqa: BLE001 — network/lookup failures are non-fatal
        return None
    firmwares = payload.get("firmwares") if isinstance(payload, dict) else None
    if not isinstance(firmwares, list):
        return None

    def _to_identity(fw: dict) -> FirmwareIdentity | None:
        try:
            size = fw.get("filesize")
            return FirmwareIdentity(
                version=str(fw.get("version", "")),
                build=str(fw.get("buildid", "")),
                device=device,
                url=str(fw.get("url", "")),
                sha1sum=str(fw.get("sha1sum", "") or "").lower(),
                sha256sum=str(fw.get("sha256sum", "") or "").lower(),
                md5sum=str(fw.get("md5sum", "") or "").lower(),
                filesize=int(size) if size is not None else None,
            )
        except Exception:  # noqa: BLE001
            return None

    if build:
        for fw in firmwares:
            if str(fw.get("buildid", "")) == build:
                ident = _to_identity(fw)
                if ident is not None:
                    return ident
        return None  # build given but not found — no fallback
    if version:
        for fw in firmwares:
            if str(fw.get("version", "")) == version:
                ident = _to_identity(fw)
                if ident is not None:
                    return ident
        return None  # version given but not found — no fallback
    # No build/version given: return the first entry.
    for fw in firmwares:
        ident = _to_identity(fw)
        if ident is not None:
            return ident
    return None


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of an on-demand IPSW hash verification."""

    path: Path
    local_sha1: str
    local_sha256: str
    local_size: int
    expected: FirmwareIdentity | None   # None = couldn't look up
    sha1_match: bool | None             # None = no expected hash
    sha256_match: bool | None
    size_match: bool | None

    @property
    def summary(self) -> str:
        """Human one-liner for logs / CLI output."""
        name = self.path.name
        if self.expected is None:
            return (
                f"{name}: could not look up expected hashes on ipsw.me; "
                f"local sha1={self.local_sha1[:12]}… "
                f"sha256={self.local_sha256[:12]}… size={self.local_size:,}"
            )
        ok = self.sha1_match and self.sha256_match and self.size_match
        if ok:
            return f"{name}: VERIFIED (sha1/sha256/size all match ipsw.me)"
        parts = []
        if self.sha1_match is False:
            parts.append("sha1 MISMATCH")
        if self.sha256_match is False:
            parts.append("sha256 MISMATCH")
        if self.size_match is False:
            parts.append(f"size MISMATCH (local {self.local_size:,} vs "
                         f"expected {self.expected.filesize or '?'})")
        return f"{name}: " + ("; ".join(parts) if parts else "no expected hashes to compare")


def verify_ipsw(
    path: Path,
    device: str | None = None,
    build: str | None = None,
    version: str | None = None,
) -> VerifyResult:
    """Hash ``path`` locally, look up expected hashes on ipsw.me, compare.

    Identity resolution order:
      1. Explicit ``device`` / ``build`` / ``version`` args.
      2. Parsed from the filename via ``parse_ipsw_filename``.
      3. Still unknown → ``expected=None`` (summary notes the lookup was
         skipped); local hashes are still computed and returned.

    Always computes local sha1 + sha256 + size. The ipsw.me lookup is
    cheap and never raises. Never raises for network/lookup failures —
    they surface as ``expected=None`` / ``match=None``.
    """
    path = Path(path)
    local_sha1 = hash_ipsw(path, "sha1")
    local_sha256 = hash_ipsw(path, "sha256")
    local_size = path.stat().st_size

    if device is None:
        parsed = parse_ipsw_filename(path.name)
        if parsed is not None:
            device, version, build = parsed

    if not device:
        return VerifyResult(
            path=path,
            local_sha1=local_sha1,
            local_sha256=local_sha256,
            local_size=local_size,
            expected=None,
            sha1_match=None,
            sha256_match=None,
            size_match=None,
        )

    expected = lookup_ipsw_me(device, build=build, version=version)
    if expected is None:
        return VerifyResult(
            path=path,
            local_sha1=local_sha1,
            local_sha256=local_sha256,
            local_size=local_size,
            expected=None,
            sha1_match=None,
            sha256_match=None,
            size_match=None,
        )

    sha1_match = (expected.sha1sum == local_sha1) if expected.sha1sum else None
    sha256_match = (expected.sha256sum == local_sha256) if expected.sha256sum else None
    size_match = (expected.filesize == local_size) if expected.filesize is not None else None
    return VerifyResult(
        path=path,
        local_sha1=local_sha1,
        local_sha256=local_sha256,
        local_size=local_size,
        expected=expected,
        sha1_match=sha1_match,
        sha256_match=sha256_match,
        size_match=size_match,
    )



def download_ipsw(
    url: str,
    dest_dir: Path,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
) -> Path:
    """Stream the URL to ``dest_dir/<basename>`` with HTTP Range-resume.

    Behavior:
      - **Cache hit:** if ``dest_dir/<basename>`` already exists and is
        non-empty, emit one 100% ``ProgressEvent`` (label ``"Using cached
        IPSW"``) and return it immediately — no network I/O. When both the
        final file and a ``.partial`` exist, the final file wins and the
        stale ``.partial`` is deleted.
      - If ``dest_dir/<basename>.partial`` exists, check size and use
        ``Range: bytes=<existing>-`` to resume. (The pre-existing
        file is left in place during download under a ``.partial``
        suffix and renamed on success.)
      - Writes to ``dest_dir/<basename>.partial`` during download
        and renames on success.
      - Up to ``MAX_DOWNLOAD_ATTEMPTS`` retries on network errors.
        After that, raises ``RestoreEngineError`` with the last
        urllib error attached.
      - ``progress_callback`` (optional) receives a ``ProgressEvent`` per
        integer-percent change while streaming (throttled: a 9.9 GB IPSW at
        64 KB chunks would otherwise emit ~150k queued signals), plus a
        final 100% event on completion. When the server sends no
        ``Content-Length``, a single ``step`` event (label ``"Downloading
        <basename>"``) is emitted instead so the UI shows a label rather than
        a frozen bar.
    """
    from apple_device_cli.restore.errors import RestoreEngineError

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = _filename_from_url(url)
    final = dest_dir / filename
    partial = dest_dir / (filename + ".partial")

    def _emit(event: ProgressEvent) -> None:
        if progress_callback is not None:
            progress_callback(event)

    if final.exists() and final.stat().st_size > 0:
        if partial.exists():
            partial.unlink()
        _emit(
            ProgressEvent(
                text=f"Using cached IPSW: {final}",
                progress=ProgressUpdate(
                    kind="percent", value=100, total=100, label="Using cached IPSW"
                ),
            )
        )
        return final

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
                content_length = resp.headers.get("Content-Length")
                try:
                    remaining = int(content_length) if content_length else 0
                except ValueError:
                    remaining = 0
                # When resuming, Content-Length is the remaining bytes only.
                total = existing + remaining
                downloaded = existing
                last_pct = -1
                label_emitted = False
                with open(partial, mode) as f:
                    while True:
                        chunk = resp.read(_DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = int(round((downloaded / total) * 100))
                            if pct != last_pct:
                                last_pct = pct
                                _emit(
                                    ProgressEvent(
                                        text=f"Downloaded {pct}%",
                                        progress=ProgressUpdate(
                                            kind="percent",
                                            value=pct,
                                            total=total,
                                            label=None,
                                        ),
                                    )
                                )
                        elif not label_emitted:
                            label_emitted = True
                            _emit(
                                ProgressEvent(
                                    text="Downloading",
                                    progress=ProgressUpdate(
                                        kind="step",
                                        value=None,
                                        total=None,
                                        label=f"Downloading {filename}",
                                    ),
                                )
                            )
                partial.replace(final)
                _emit(
                    ProgressEvent(
                        text="Download complete",
                        progress=ProgressUpdate(
                            kind="percent", value=100, total=total or 100, label=None
                        ),
                    )
                )
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


def _stream_subprocess_to_callback(proc, progress_callback) -> str:
    """Read lines from ``proc.stdout`` until EOF. Returns the full
    output as one string. Each line is also passed to
    ``progress_callback`` as a ``ProgressEvent`` for streaming.

    Runs synchronously in the caller's thread; safe to use from a
    QThread worker (which is what the GUI does).
    """
    output_lines: list[str] = []
    if proc.stdout is None:
        return ""
    for line in proc.stdout:
        stripped = line.rstrip("\n")
        output_lines.append(line)
        progress_callback(
            ProgressEvent(text=stripped, progress=parse_progress_line(stripped))
        )
    return "".join(output_lines)


def restore_device(
    ipsw_path: Path,
    cache_dir: Path,
    progress_callback: Callable[[ProgressEvent], None],
    udid: str | None = None,
    ecid: str | None = None,
) -> RestoreResult:
    """Run ``idevicerestore -e -P -C <cache_dir> --logfile=<log>`` on
    ``ipsw_path`` and stream stdout/stderr to ``progress_callback`` as
    ``ProgressEvent`` objects (see ``parse_progress_line`` for the two
    supported progress formats).

    Device targeting is mode-aware:

      - ``ecid`` (``idevicerestore -i``): used verbatim when given. ``-i``
        works in normal, recovery, and DFU modes, so callers restoring a
        recovery-mode device (invisible to usbmuxd, so no UDID available)
        pass the ECID here.
      - Normal mode: ``-u <udid>`` (the only mode ``-u`` supports).
      - Recovery/restore/DFU mode (detected via USB product ID): the ECID
        is resolved from the device and ``-i <ecid>`` is used instead —
        ``-u`` would fail with "Unable to discover device mode".
      - Unknown mode: ``-u <udid>`` first; on that failure signature the
        command is retried once with ``-i <ecid>`` (resolved the same way).

    ``-P`` (plain progress) is passed so the engine gets clean
    ``PROGRESS: x/y`` / ``STEP: ...`` lines to drive a progress bar; the
    default-format parser is the fallback if ``idevicerestore`` ever drops
    the flag.

    No timeout. ``stdin=DEVNULL`` so ``idevicerestore`` cannot prompt on a
    TTY. SIGINT (Ctrl-C) reaches the child via the parent's signal
    disposition — ``subprocess.Popen`` inherits it by default.

    Falls back to ``restore_device_via_pymd3`` if ``idevicerestore`` is not
    on PATH.
    """
    if not shutil.which("idevicerestore"):
        progress_callback(
            ProgressEvent(
                text="idevicerestore not found, falling back to pymobiledevice3 "
                "(slower, fewer features; known broken on iOS 26)."
            )
        )
        return restore_device_via_pymd3(udid, ipsw_path, cache_dir, progress_callback)

    logs_dir = cache_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = udid or ecid or "unknown"
    log_path = logs_dir / f"restore_{slug}_{ts}.log"

    base = [
        "idevicerestore",
        "-e",
        "-y",
        "-P",
        "-C", str(cache_dir),
        "--logfile", str(log_path),
    ]

    if ecid:
        target_args = ["-i", _strip_ecid_prefix(ecid)]
    elif udid:
        mode = detect_device_mode(udid)
        if mode in ("recovery", "restore", "dfu"):
            resolved = _device_ecid(udid)
            if not resolved:
                from apple_device_cli.restore.errors import RestoreEngineError

                raise RestoreEngineError(
                    f"Device appears to be in {mode} mode; could not determine "
                    "its ECID to target it. Run `irecovery -q` to see the ECID "
                    "and pass it via --ecid."
                )
            target_args = ["-i", resolved]
        else:
            # Normal mode (or an un-detectable one) — `-u` is the only thing
            # that works there. Unknown-mode failures are retried with `-i`
            # below.
            target_args = ["-u", udid]
    else:
        from apple_device_cli.restore.errors import RestoreEngineError

        raise RestoreEngineError(
            "restore_device requires a UDID (Normal mode) or an ECID "
            "(recovery/restore/DFU mode) to target the device."
        )

    proc = _popen_capture(base + target_args + [str(ipsw_path)])
    output = _stream_subprocess_to_callback(proc, progress_callback)
    proc.wait()

    if proc.returncode == 0:
        return RestoreResult(
            success=True,
            udid=udid,
            ipsw_path=ipsw_path,
            log_excerpt="\n".join(output.splitlines()[-50:]),
        )

    # Unknown-mode retry: `-u` against a Recovery-mode device fails with
    # "Unable to discover device mode". Resolve the ECID and retry once.
    if (
        ecid is None
        and udid is not None
        and target_args[:1] == ["-u"]
        and _failed_to_discover_mode(output)
    ):
        resolved = _device_ecid(udid)
        if resolved:
            progress_callback(
                ProgressEvent(
                    text="Mode discovery failed; retrying once with ECID "
                    f"{resolved}."
                )
            )
            retry = _popen_capture(base + ["-i", resolved] + [str(ipsw_path)])
            retry_output = _stream_subprocess_to_callback(retry, progress_callback)
            retry.wait()
            if retry.returncode == 0:
                return RestoreResult(
                    success=True,
                    udid=udid,
                    ipsw_path=ipsw_path,
                    log_excerpt="\n".join(retry_output.splitlines()[-50:]),
                )
            last_lines = "\n".join(retry_output.splitlines()[-50:])
            return RestoreResult(
                success=False,
                udid=udid,
                ipsw_path=ipsw_path,
                error=(
                    f"idevicerestore exited with code {retry.returncode}. "
                    f"Full log: {log_path}\n--- last 50 lines ---\n{last_lines}"
                ),
                log_excerpt=last_lines,
            )

    # Failure: include the log file path so the user can read the full
    # output (often 50-200 MB for a full restore).
    last_lines = "\n".join(output.splitlines()[-50:])
    return RestoreResult(
        success=False,
        udid=udid,
        ipsw_path=ipsw_path,
        error=(
            f"idevicerestore exited with code {proc.returncode}. "
            f"Full log: {log_path}\n--- last 50 lines ---\n{last_lines}"
        ),
        log_excerpt=last_lines,
    )


def restore_device_via_pymd3(
    udid: str,
    ipsw_path: Path,
    cache_dir: Path,
    progress_callback: Callable[[ProgressEvent], None],
) -> RestoreResult:
    """Pure-Python fallback using pymobiledevice3.restore.restore.Restore.

    KNOWN-BRITTLE on iOS 26 — the upstream Restore API throws
    "Could not create Reverse Proxy" against modern devices. This
    function exists so the engine has a fallback if idevicerestore
    is missing, but the user should expect it to fail on iOS 26
    with that error.

    Implementation note: this is a thin async wrapper around
    ``pymobiledevice3.restore.restore.Restore.update()``. The
    actual call is wrapped in a try/except so any failure becomes
    a clean ``RestoreResult(success=False, ...)``.

    In this iteration we surface a clear "not implemented" error so
    the engine stays importable on hosts without idevicerestore.
    A real pymd3 implementation can land in a follow-up.
    """
    progress_callback(
        ProgressEvent(
            text="pymobiledevice3 restore path is the fallback. It is known "
            "to fail on iOS 26 — if the restore fails here, install "
            "libimobiledevice (brew install libimobiledevice) and try again "
            "with the idevicerestore path."
        )
    )
    return RestoreResult(
        success=False,
        udid=udid,
        ipsw_path=ipsw_path,
        error=(
            "pymobiledevice3 restore path is not implemented in this "
            "iteration. Install idevicerestore (brew install "
            "libimobiledevice) and use the primary restore path. "
            "This fallback exists only to keep the engine importable "
            "on hosts without idevicerestore."
        ),
    )


# --- Device mode detection and recovery-mode entry/exit ---------------------

# Apple USB product IDs for the different boot modes. ``0x12a8`` is the classic
# normal-mode PID; ``0x12ab`` is the PID newer devices present in normal mode.
# ``0x1280``-``0x1283`` are iBSS/iBEC (recovery), ``0x12ac`` is the
# com.apple.mobile.restored (restore) mode, and ``0x1227`` is DFU.
_MODE_BY_PID = {
    0x12a8: "normal",
    0x12ab: "normal",
    0x1280: "recovery",
    0x1281: "recovery",
    0x1282: "recovery",
    0x1283: "recovery",
    0x12ac: "restore",
    0x1227: "dfu",
}

# iBoot (recovery) mode product IDs. Recovery-mode devices respond to a USB
# reset (D+/D- toggle) and re-enumerate into Normal mode. DFU devices do NOT —
# they need a hard reset or power cycle.
_RECOVERY_PIDS = (0x1280, 0x1281, 0x1282, 0x1283)
_DFU_PID = 0x1227

_APPLE_VENDOR_ID = 0x05ac


# Structured iBoot USB serial descriptors (recovery mode) carry the device's
# actual serial inside ``srnm:[...]`` and its ECID inside ``ecid:...``, e.g.:
#   sdom:01 cpid:8120 cprv:11 cpfm:03 scep:01 bdid:10 ecid:00094daa01d80032 ibfl:3d sika:00 srnm:[jxmwm7422v]
# A Normal-mode lockdown UDID never matches these — the descriptor has no UDID.
_SRNM_RE = re.compile(r"srnm:\[([^\]]+)\]")
_ECID_RE = re.compile(r"ecid:([0-9a-fA-F]+)")


def _srnm_from_descriptor(serial: str) -> str | None:
    """Return the ``srnm:[...]`` value from a structured iBoot descriptor
    (e.g. ``jxmwm7422v``), or None when the string has no such field."""
    match = _SRNM_RE.search(serial or "")
    return match.group(1) if match else None


def _normalize_serial(value: str) -> str:
    """Strip NUL padding, whitespace, and dashes so serials compare cleanly.

    Recovery-mode USB serials are structured iBoot descriptor strings whose
    real serial lives inside ``srnm:[...]``. When that field is present, the
    inner value is used as the canonical serial so comparisons match the SRNM
    (e.g. ``JXMWM7422V``) instead of the whole descriptor.
    """
    raw = (value or "").replace("\x00", "").strip()
    srnm = _srnm_from_descriptor(raw)
    if srnm is not None:
        raw = srnm
    return raw.replace("-", "").lower()


def _ecid_from_descriptor(serial: str) -> str:
    """Extract the ECID (bare hex, no ``0x`` prefix) from a structured iBoot
    USB descriptor string, or "" when the field is absent."""
    match = _ECID_RE.search(serial or "")
    return match.group(1) if match else ""


def _iter_apple_usb_devices():
    """Yield ``(serial, product_id, bus, device)`` per Apple USB device on the bus.

    ``serial`` is the device's USB serial string (normalized); ``product_id``
    is the USB product ID (see ``_MODE_BY_PID``); ``bus`` is the USB bus
    number; ``device`` is the pyusb ``Device`` object (used for USB resets).

    pyusb is a hard dependency of pymobiledevice3, so it is always present at
    runtime. Defensive try/except keeps this helper usable on hosts where
    libusb is missing or a device's string descriptors are unreadable
    (permission issues) — both show up as per-device exceptions, not crashes.
    """
    try:
        from usb.core import find as usb_find
    except Exception:
        return
    try:
        devices = usb_find(find_all=True) or ()
    except Exception:
        return
    for device in devices:
        try:
            if int(device.idVendor) != _APPLE_VENDOR_ID:
                continue
            serial = _normalize_serial(device.serial_number)
            pid = int(device.idProduct)
            bus = int(device.bus)
        except Exception:
            continue
        yield serial, pid, bus, device


def _strip_ecid_prefix(value: str) -> str:
    """Normalize an ECID to lowercase hex WITH the ``0x`` prefix.

    ``idevicerestore -i`` requires the ``0x`` prefix (e.g.
    ``0x00094daa01d80032``); the bare-hex form ``00094daa01d80032`` is
    rejected with "Could not parse ECID". Always emit the prefixed form.
    """
    stripped = (value or "").strip().lower()
    if not stripped:
        return ""
    if stripped.startswith("0x"):
        return "0x" + stripped[2:]
    return "0x" + stripped


def _normalize_ecid(value: str) -> str:
    """Normalize an ECID for equality comparison (``0x``-prefixed lowercase
    hex, dashes stripped)."""
    return _strip_ecid_prefix(value).replace("-", "").lower()


def _failed_to_discover_mode(output: str) -> bool:
    """True if ``output`` shows ``idevicerestore`` could not discover the
    device's mode — the failure signature when ``-u <udid>`` is used against
    a device in Recovery mode."""
    return "unable to discover device mode" in output.lower()


_IRECOVERY_QUERY_TIMEOUT = 10


def _query_recovery_ecid() -> str | None:
    """Query ``irecovery -q`` for the connected recovery device's ECID.

    ``irecovery`` (libimobiledevice's recovery utility) talks to the first
    Recovery/DFU-mode device on the USB bus. Returns the ECID hex string
    without the ``0x`` prefix, or None when ``irecovery`` is not installed,
    the query times out, or the ECID cannot be parsed.
    """
    try:
        proc = subprocess.run(
            ["irecovery", "-q"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=_IRECOVERY_QUERY_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in proc.stdout.splitlines():
        text = line.strip()
        if text.lower().startswith("ecid:"):
            return _strip_ecid_prefix(text.split(":", 1)[1])
    return None


def _device_ecid(udid: str | None = None) -> str | None:
    """Resolve a device to its ECID (hex with the ``0x`` prefix), or None.

    Recovery/restore/DFU-mode devices are invisible to usbmuxd, so
    ``idevicerestore`` cannot target them with ``-u <udid>`` — only by ECID
    (``-i``). ``idevicerestore -i`` requires the ``0x`` prefix, so every
    returned ECID is emitted in that form. This helper resolves the ECID:

    1. Match ``udid`` against each Apple USB device's serial number. In
       Normal mode the USB serial equals the UDID; in Recovery mode the
       descriptor's ``srnm:[...]`` field (e.g. ``JXMWM7422V``) matches when
       the caller passes that instead. When the matched descriptor carries an
       ``ecid:...`` field, that is returned directly (no subprocess needed).
    2. No serial match (or no ``udid``): when any Recovery/DFU-mode device
       is present, query it directly with ``irecovery -q``.

    Returns None when the device is not on the bus or the ECID cannot be
    determined (``irecovery`` missing, etc.).
    """
    target = _normalize_serial(udid) if udid else ""
    recovery_present = False
    for serial, pid, _bus, _device in _iter_apple_usb_devices():
        ecid = _ecid_from_descriptor(serial)
        if target and _normalize_serial(serial) == target:
            return _strip_ecid_prefix(ecid) or _query_recovery_ecid()
        if pid in _RECOVERY_PIDS or pid == _DFU_PID:
            recovery_present = True
    if recovery_present:
        return _query_recovery_ecid()
    return None


def recovery_device_descriptor() -> tuple[str, str] | None:
    """Return ``(srnm, ecid)`` for the first Recovery-mode USB device on the
    bus, or None when none is present.

    Recovery-mode devices are invisible to usbmuxd (lockdown isn't running),
    so they never appear in normal device enumeration. This helper extracts
    their SRNM serial and ECID from the structured iBoot descriptor so the
    GUI can present a selectable "Recovery mode" entry. ``ecid`` is hex with
    the ``0x`` prefix (as ``idevicerestore -i`` requires), or "" when the
    descriptor lacks the field.
    """
    for serial, pid, _bus, _device in _iter_apple_usb_devices():
        if pid in _RECOVERY_PIDS:
            srnm = _srnm_from_descriptor(serial) or _normalize_serial(serial)
            return srnm, _strip_ecid_prefix(_ecid_from_descriptor(serial))
    return None


def detect_device_mode(udid: str) -> str:
    """Detect the USB boot mode of ``udid``.

    Returns one of ``"normal"``, ``"recovery"``, ``"restore"``, ``"dfu"``,
    or ``"unknown"``. The mode is derived from the device's USB product ID
    (Apple vendor 0x05ac), matched by serial number — which equals the UDID
    while the device runs in Normal mode. Never raises: any lookup problem
    (no matching device, unreadable descriptors, missing libusb) yields
    ``"unknown"``.

    Recovery-mode devices cannot be matched by a Normal-mode lockdown UDID:
    their USB serial is a structured iBoot descriptor (``sdom:01 ... srnm:[...]
    ecid:...``) carrying the SRNM serial and ECID, not the UDID, so
    ``_normalize_serial(serial) == _normalize_serial(udid)`` fails against a
    lockdown UDID. That is EXPECTED — ``"unknown"`` for a recovery device
    looked up by UDID is correct; ``detect_recovery_devices_present()`` and
    ``recovery_device_descriptor()`` are the real signals (and the SRNM
    matches when the caller passes that instead of a UDID).
    """
    target = _normalize_serial(udid)
    for serial, pid, _bus, _device in _iter_apple_usb_devices():
        if _normalize_serial(serial) == target:
            return _MODE_BY_PID.get(pid, "unknown")
    return "unknown"


def detect_recovery_devices_present() -> bool:
    """Return True if any iBoot-mode USB device is on the bus.

    "iBoot-mode" covers Recovery (PID 0x1280-0x1283) and DFU (0x1227) — both
    states are invisible to usbmuxd, so the Restore tab's device dropdown
    cannot show the device. Used by the GUI's "Exit Recovery (any device)"
    button to enable itself when a device has gone into Recovery. The button
    is also clickable unconditionally, but this helper is useful for showing
    a status indicator (e.g. "Recovery device detected") in the GUI.

    Never raises: any lookup problem yields False.
    """
    try:
        for _serial, pid, _bus, _device in _iter_apple_usb_devices():
            if pid in _RECOVERY_PIDS or pid == _DFU_PID:
                return True
    except Exception:
        return False
    return False


def exit_recovery_mode(udid: str | None = None) -> list[str]:
    """Reset recovery-mode device(s) to Normal mode via ``irecovery --normal``.

    If ``udid`` is provided, only reset that device. The ``udid`` argument
    may be the recovery descriptor's SRNM serial (``JXMWM7422V``) or its
    ECID (``0x...`` or bare hex) — both appear in the structured iBoot USB
    serial string (``srnm:[...]`` / ``ecid:...``). A Normal-mode lockdown
    UDID will NOT match (the recovery descriptor does not carry it). If
    ``udid`` is None (the default), scan for any iBoot-mode USB device
    (recovery PID 0x1280-0x1283) and reset every recovery-mode device found.

    ``irecovery --normal`` (libirecovery) sends the "boot to normal" command
    to the recovery iBSS, which re-enumerates the device in Normal mode. A
    bare USB reset (``device.reset()``) would instead reboot iBSS straight
    back into the recovery loop — that is why plain USB resets visibly "do
    nothing" for exit-recovery.

    Returns a list of the reset devices' USB serials (the raw descriptor
    string as yielded by ``_iter_apple_usb_devices``; for a real device this
    is the structured iBoot descriptor, not a Normal-mode lockdown UDID).
    Raises ``RestoreEngineError`` when no recovery-mode device is found, when
    the only device found is in DFU (``irecovery --normal`` cannot exit DFU —
    the device needs a power cycle), when ``irecovery`` is missing from PATH,
    or when the command itself fails.
    """
    from apple_device_cli.restore.errors import RestoreEngineError

    if not shutil.which("irecovery"):
        raise RestoreEngineError(
            "`irecovery` is required to exit recovery mode. Install it with: "
            "brew install libirecovery (or `apt install libirecovery-utils` "
            "on Debian/Ubuntu) — the package that provides the `irecovery` CLI."
        )

    recovery: list[str] = []
    dfu: list[str] = []
    for serial, pid, _bus, _device in _iter_apple_usb_devices():
        if pid in _RECOVERY_PIDS:
            recovery.append(serial)
        elif pid == _DFU_PID:
            dfu.append(serial)

    if udid:
        norm = _normalize_serial(udid)
        udid_ecid = _normalize_ecid(udid)
        matches = [
            serial
            for serial in recovery
            if _normalize_serial(serial) == norm
            or (udid_ecid and _normalize_ecid(_ecid_from_descriptor(serial)) == udid_ecid)
        ]
        dfu_matches = [
            serial for serial in dfu if _normalize_serial(serial) == norm
        ]
        if not matches and not dfu_matches:
            raise RestoreEngineError(
                f"No recovery device found matching {udid}. Put the device "
                "into recovery (hold Power + Home / Side buttons) and try again."
            )
        if not matches:
            raise RestoreEngineError(
                "Device is in DFU mode, not Recovery — `irecovery --normal` "
                "cannot exit DFU; power cycle the device manually."
            )
        targets = matches
    else:
        if not recovery and not dfu:
            raise RestoreEngineError(
                "No recovery device found. Put the device into recovery "
                "(hold Power + Home / Side buttons) and try again."
            )
        if not recovery:
            raise RestoreEngineError(
                "Device is in DFU mode, not Recovery — `irecovery --normal` "
                "cannot exit DFU; power cycle the device manually."
            )
        targets = recovery

    reset_serials: list[str] = []
    for serial in targets:
        proc = subprocess.run(
            ["irecovery", "--normal"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0:
            raise RestoreEngineError(
                f"`irecovery --normal` failed (code {proc.returncode}) for "
                f"device {serial}.\nOutput:\n{proc.stdout}"
            )
        reset_serials.append(serial)
    return reset_serials


def enter_recovery_mode(udid: str) -> None:
    """Send ``udid`` into recovery mode via lockdownd's EnterRecovery request.

    Connects to the device over usbmux (reusing the iOS 26 pair-on-failure
    helper) and issues the ``EnterRecovery`` lockdown operation, which makes
    the device reboot into iBSS/iBEC (recovery mode). Raises
    ``RestoreEngineError`` on connection or request failure.

    Both the lockdown connect and the ``enter_recovery()`` request run in a
    single event loop. pymobiledevice3 binds futures to the loop that created
    the lockdown client, so two ``asyncio.run()`` calls (one per operation)
    would raise "got Future attached to a different loop".
    """
    from apple_device_cli.restore.errors import RestoreEngineError

    async def _do() -> None:
        lockdown = await _create_using_usbmux_with_pair_retry(udid)
        await lockdown.enter_recovery()

    try:
        asyncio.run(_do())
    except ConnectionError as exc:
        raise RestoreEngineError(
            f"Could not connect to device {udid} to enter recovery mode. "
            f"Underlying error: {exc}"
        ) from exc
    except Exception as exc:
        raise RestoreEngineError(
            f"Failed to enter recovery mode on {udid}: {exc}"
        ) from exc
