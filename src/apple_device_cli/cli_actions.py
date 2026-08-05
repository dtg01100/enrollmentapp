"""Pure business-logic functions extracted from the Typer CLI commands.

The Typer command wrappers in ``cli.py`` are now thin: they do option parsing,
call into one of these pure functions, and format the result for the user.
The functions here have no ``typer.*`` dependencies — they take optional
``manager`` parameters so tests can inject mock managers, and they raise typed
exceptions so callers can decide how to surface errors.

Rule: do NOT add ``typer`` imports here. The only output these functions
produce is the data the caller needs to display.
"""
from __future__ import annotations

import plistlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apple_device_cli.orgs.identity import generate_org_identity
from apple_device_cli.orgs.manager import Organization, OrganizationManager


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class _OrgError(Exception):
    """Base class for organization-related errors raised by cli_actions."""

    def __init__(self, name: str, message: str | None = None):
        self.name = name
        super().__init__(message or f"Organization '{name}'")


class OrgNotFoundError(_OrgError, LookupError):
    """Raised when an org lookup fails (get_org returns None, delete_org returns False)."""

    def __init__(self, name: str):
        super().__init__(name, f"Organization not found: {name}")


class OrgAlreadyExistsError(_OrgError, ValueError):
    """Raised when save_org refuses to overwrite an existing org."""

    def __init__(self, name: str):
        super().__init__(name, f"Organization '{name}' already exists")


class _WifiConfigError(Exception):
    """Base class for WiFi mobileconfig errors."""

    def __init__(self, path: str, message: str | None = None):
        self.path = path
        super().__init__(message or f"WiFi config: {path}")


class WifiConfigNotFoundError(_WifiConfigError, FileNotFoundError):
    """Raised when the WiFi mobileconfig file does not exist on disk."""

    def __init__(self, path: str):
        super().__init__(path, f"WiFi config file not found: {path}")


class WifiConfigInvalidError(_WifiConfigError, ValueError):
    """Raised when the WiFi mobileconfig exists but is not a valid plist."""

    def __init__(self, path: str):
        super().__init__(path, f"Invalid mobileconfig: {path} is not a valid plist")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateOrgResult:
    """Returned by create_org — fields the caller needs to display."""

    name: str
    mdm_url: str | None
    checkin_url: str | None
    mdm_topic: str | None
    wifi_config_path: str | None


@dataclass(frozen=True)
class SetOrgWifiResult:
    """Returned by set_org_wifi — record of the attached WiFi config."""

    name: str
    wifi_config_path: str


@dataclass(frozen=True)
class GenerateOrgResult:
    """Returned by generate_org — full record of the new identity + org."""

    name: str
    org_id: str | None
    mdm_url: str | None
    checkin_url: str | None
    mdm_topic: str | None
    mdm_description: str | None
    cert_path: str
    key_path: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_manager(manager: OrganizationManager | None) -> OrganizationManager:
    """Return the caller-supplied manager, or a real default one."""
    if manager is None:
        return OrganizationManager()
    return manager


def _wrap_already_exists(name: str) -> None:
    """Translate the bare ValueError from save_org into a typed exception."""
    raise OrgAlreadyExistsError(name)


# ---------------------------------------------------------------------------
# set_org_field
# ---------------------------------------------------------------------------


def set_org_field(
    manager: OrganizationManager,
    name: str,
    field_name: str,
    value: str,
    label: str,  # noqa: ARG001 — reserved for future label-aware presentation
) -> None:
    """Update one field on an existing org and save it.

    Args:
        manager: OrganizationManager (real or spec'd mock).
        name: Org name to update.
        field_name: Attribute on Organization to set (e.g. "mdm_url").
        value: String value to assign.
        label: Human-readable label for the field (used by callers, not here).

    Raises:
        OrgNotFoundError: If no org with ``name`` exists.
        ValueError: Re-raised from ``manager.save_org`` (e.g. lock failure).
    """
    org = manager.get_org(name)
    if not org:
        raise OrgNotFoundError(name)
    setattr(org, field_name, value)
    manager.save_org(org, overwrite=True)


# ---------------------------------------------------------------------------
# create_org
# ---------------------------------------------------------------------------


def create_org(
    *,
    manager: OrganizationManager | None = None,
    name: str,
    org_id: str | None,
    address: str | None,
    phone: str | None,
    email: str | None,
    mdm_url: str | None,
    checkin_url: str | None,
    mdm_topic: str | None,
    mdm_description: str | None,
    cert: str | None,
    key: str | None,
    wifi_config: str | None,
) -> CreateOrgResult:
    """Build an Organization and save it via the manager.

    Raises:
        OrgAlreadyExistsError: If an org with ``name`` already exists.
        ValueError: Other save_org errors propagate.
    """
    mgr = _resolve_manager(manager)
    org = Organization(
        name=name,
        org_id=org_id,
        address=address,
        phone=phone,
        email=email,
        mdm_url=mdm_url,
        checkin_url=checkin_url,
        mdm_topic=mdm_topic,
        mdm_description=mdm_description,
    )
    if cert:
        org.cert_path = str(Path(cert).resolve())
    if key:
        org.key_path = str(Path(key).resolve())
    if wifi_config:
        org.wifi_config_path = str(Path(wifi_config).resolve())

    try:
        mgr.save_org(org)
    except ValueError as exc:
        msg = str(exc)
        if "already exists" in msg:
            _wrap_already_exists(name)
        raise

    return CreateOrgResult(
        name=org.name,
        mdm_url=org.mdm_url,
        checkin_url=org.checkin_url,
        mdm_topic=org.mdm_topic,
        wifi_config_path=org.wifi_config_path,
    )


# ---------------------------------------------------------------------------
# delete_org
# ---------------------------------------------------------------------------


def delete_org(
    manager: OrganizationManager,
    name: str,
) -> bool:
    """Delete an organization. Returns True if deleted, raises if not found.

    Raises:
        OrgNotFoundError: If ``manager.delete_org`` returns False.
    """
    if manager.delete_org(name):
        return True
    raise OrgNotFoundError(name)


# ---------------------------------------------------------------------------
# import_org
# ---------------------------------------------------------------------------


def import_org(
    manager: OrganizationManager,
    path: str,
    password: str = "",
) -> Organization:
    """Import an org from an Apple Configurator ``.organization`` file, ZIP, or directory.

    Empty ``password`` strings are normalized to the default ``"password"``
    to match the original CLI behavior.

    Raises:
        ValueError: All underlying errors (invalid path, parse failure, wrong
            password) propagate as ``ValueError`` from the manager.
    """
    effective_password = password or "password"
    return manager.import_org(path, effective_password)


# ---------------------------------------------------------------------------
# import_mobileconfig
# ---------------------------------------------------------------------------


def import_mobileconfig(
    manager: OrganizationManager,
    path: str,
) -> Organization:
    """Import an org from an MDM ``.mobileconfig`` file.

    Raises:
        ValueError: All underlying errors (missing file, parse failure, org
            already exists) propagate as ``ValueError`` from the manager.
    """
    return manager.import_mobileconfig(path)


# ---------------------------------------------------------------------------
# set_org_wifi
# ---------------------------------------------------------------------------


def set_org_wifi(
    manager: OrganizationManager,
    name: str,
    path: str,
) -> SetOrgWifiResult:
    """Attach a WiFi mobileconfig to an existing org.

    Copies the file into the org's directory and updates the org's
    ``wifi_config_path``. Saves the org metadata.

    Raises:
        OrgNotFoundError: If no org with ``name`` exists.
        WifiConfigNotFoundError: If ``path`` doesn't exist on disk.
        WifiConfigInvalidError: If ``path`` exists but is not a valid plist.
    """
    org = manager.get_org(name)
    if not org:
        raise OrgNotFoundError(name)

    wifi_path = Path(path)
    if not wifi_path.exists():
        raise WifiConfigNotFoundError(str(wifi_path))

    try:
        with open(wifi_path, "rb") as f:
            plistlib.load(f)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise WifiConfigInvalidError(str(wifi_path)) from exc

    org_dir = manager.orgs_dir / manager._sanitize_name(name)
    dest_wifi = org_dir / "wifi.mobileconfig"
    shutil.copy(wifi_path, dest_wifi)

    org.wifi_config_path = str(dest_wifi)
    org.save(org_dir, skip_copy=True)

    return SetOrgWifiResult(name=org.name, wifi_config_path=str(dest_wifi))


# ---------------------------------------------------------------------------
# generate_org
# ---------------------------------------------------------------------------


def generate_org(
    *,
    manager: OrganizationManager | None = None,
    name: str,
    org_id: str | None,
    mdm_url: str | None,
    checkin_url: str | None,
    mdm_topic: str | None,
    mdm_description: str | None,
    valid_days: int,
    identity_factory: Any = None,
) -> GenerateOrgResult:
    """Generate a self-signed identity for an org and persist it.

    The caller is responsible for confirming overwrite with the user when an
    existing org already has cert/key — this pure function regen-and-overwrites
    unconditionally. Decisions about user intent belong at the CLI layer.

    Args:
        identity_factory: Optional callable ``(name, valid_days) -> (cert_der, key_der)``.
            Defaults to ``generate_org_identity``. Tests can pass a fake.

    Raises:
        OSError: If writing the cert or key file fails.
    """
    mgr = _resolve_manager(manager)
    factory = identity_factory or generate_org_identity

    cert_der, key_der = factory(name, valid_days)

    org_dir = mgr.orgs_dir / mgr._sanitize_name(name)
    if org_dir.exists():
        shutil.rmtree(org_dir)
    org_dir.mkdir(parents=True, exist_ok=True)

    (org_dir / "cert.der").write_bytes(cert_der)
    (org_dir / "key.der").write_bytes(key_der)

    org = Organization(
        name=name,
        org_id=org_id,
        mdm_url=mdm_url,
        checkin_url=checkin_url,
        mdm_topic=mdm_topic,
        mdm_description=mdm_description,
        cert_path=str(org_dir / "cert.der"),
        key_path=str(org_dir / "key.der"),
    )
    org.save(org_dir, skip_copy=True)

    return GenerateOrgResult(
        name=org.name,
        org_id=org.org_id,
        mdm_url=org.mdm_url,
        checkin_url=org.checkin_url,
        mdm_topic=org.mdm_topic,
        mdm_description=org.mdm_description,
        cert_path=org.cert_path,
        key_path=org.key_path,
    )
