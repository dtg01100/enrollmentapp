# ios-enroll AGENTS.md

**Version:** 1.0
**Date:** 2026-05-04
**Purpose:** Technical reference for ios-enroll project development

---

## Project Overview

**ios-enroll** is an iOS device supervised enrollment CLI for Linux — an Apple Configurator alternative.

- **Language:** Python 3.10+
- **Architecture:** Typer CLI application
- **Package:** `src/apple_device_cli`
- **Entry point:** `ios-enroll = "apple_device_cli.cli:main"`

---

## Quick Setup

```bash
# Install
uv tool install .

# Run (no install)
python -m apple_device_cli.cli

# Test (requires PYTHONPATH)
PYTHONPATH=src python -m pytest tests/ -v
```

---

## Architecture

```
User Input (CLI)
    |
    v
Typer App (cli.py)
    |
    +-- device/      # Device enumeration, info, state
    +-- enrollment/  # Supervised pairing, activation, skip panes
    +-- orgs/        # Organization management, identity
    +-- restore/     # Erase, update, restore operations
    +-- core/        # Exceptions, redaction utilities
```

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `src/apple_device_cli/` | Main package |
| `src/apple_device_cli/cli.py` | Typer app entry point |
| `src/apple_device_cli/core/` | Exceptions, redaction |
| `src/apple_device_cli/device/` | Device connection, info, state |
| `src/apple_device_cli/enrollment/` | Supervised, activation, skip panes |
| `src/apple_device_cli/orgs/` | Organization manager, identity |
| `src/apple_device_cli/restore/` | Erase, update, restore |
| `tests/` | pytest test suite |
| `~/.config/apple_device_cli/orgs/` | Default org storage |

---

## Code Style

**Python Conventions:**

- Python 3.10+ with type hints
- Use `from __future__ import annotations` for forward references
- 4 spaces indentation (no tabs)
- Docstrings for modules and public functions
- Dataclasses for data structures

**Module Template:**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MyClass:
    """Brief description of purpose."""
    name: str
    value: int | None = None

    def method(self) -> str:
        """Public method description."""
        return f"{self.name}: {self.value}"
```

---

## Module Naming Conventions

| Module | Purpose |
|--------|---------|
| `device/connection.py` | Device enumeration, pairing |
| `device/info.py` | DeviceInfo dataclass |
| `device/state.py` | Device state utilities |
| `enrollment/activation.py` | Device activation |
| `enrollment/skip_panes.py` | VALID_PANES, PRESETS, resolve_skip_panes() |
| `enrollment/supervised.py` | make_supervised() via pymobiledevice3 |
| `enrollment/flows.py` | Enrollment flow utilities |
| `orgs/manager.py` | OrganizationManager, Organization |
| `orgs/identity.py` | generate_org_identity(), load_cert_info() |
| `restore/erase.py` | Erase/update/restore operations |
| `core/exceptions.py` | AppleDeviceError, EnrollmentError |
| `core/redaction.py` | Address, email, identifier redaction |

---

## Testing

**Before Committing:**

```bash
# Run all tests (PYTHONPATH required)
PYTHONPATH=src python -m pytest tests/ -v

# Run specific test file
PYTHONPATH=src python -m pytest tests/test_org_manager.py -v

# Run with coverage
PYTHONPATH=src python -m pytest tests/ -v --cov=apple_device_cli
```

**Test Requirements:**

- Tests mock `subprocess.run` for mobileconfig import
- Tests use temp directories for orgs to avoid polluting `~/.config`
- Some tests verify exact error message text (grep for `match=`)

---

## Commit Format

```
type(scope): brief description

Problem: What was broken/incomplete
Solution: How you fixed it
Testing: How you verified the fix
```

**Types:** `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

**Example:**

```bash
git add -A && git commit -m "fix(orgs): preserve MDM fields on save

Problem: MDM fields were lost during org save/load roundtrip
Solution: Added mdm_url, checkin_url, mdm_topic to to_dict/from_dict
Testing: test_organization_save_load_with_mdm_fields passes"
```

---

## Key Classes & Functions

### Organization (orgs/manager.py)
```python
@dataclass
class Organization:
    name: str
    org_id, address, phone, email: str | None
    mdm_url, checkin_url, mdm_topic, identity_ref, mdm_description: str | None
    cert_path, key_path: str | None
    created_at: str

    def to_dict(self) -> dict
    @classmethod def from_dict(cls, data: dict) -> Organization
    def save(self, org_dir: Path, skip_copy: bool = False)
    @classmethod def load(cls, org_dir: Path) -> Organization
```

### OrganizationManager (orgs/manager.py)
```python
class OrganizationManager:
    def __init__(self, orgs_dir: Path | None = None)
    def list_orgs() -> list[Organization]
    def get_org(name: str) -> Organization | None
    def save_org(org: Organization, overwrite: bool = False)
    def delete_org(name: str) -> bool
    def import_org(path, password="password") -> Organization
    def export_org(name, dest_path) -> bool
```

### Skip Panes (enrollment/skip_panes.py)
```python
VALID_PANES = {"location", "restore", "sim-setup", "appleid", ...}
PRESETS = {"minimal", "standard", "all"}
resolve_skip_panes(preset: str | None, extra_panes: list[str] | None) -> list[str]
```

---

## Critical Behaviors & Gotchas

### Org Storage
- Each org is a directory at `~/.config/apple_device_cli/orgs/<sanitized_name>/`
- Contains: `org.json` (metadata), `cert.der` (optional), `key.der` (optional)
- **Important**: `save()` writes `org.json` but intentionally omits `cert_path`/`key_path`
- Tests depend on this layout — don't change without updating tests

### Import Flows
- `.organization` files (Apple Configurator): uses PKCS12 with default password `"password"`
- `.mobileconfig` files: uses `openssl smime -verify -inform DER -noverify`
- Both raise `ValueError` with specific messages that tests assert against

### Error Messages (Don't Change Without Tests)
- `"Organization 'X' already exists"` (mobileconfig duplicate)
- `"Missing PayloadOrganization in mobileconfig"`
- `"Failed to decode identity (wrong password?)"`
- `"Failed to parse mobileconfig: {stderr}"`

### pymobiledevice3
- Primary device interaction library
- `connection.py` uses `create_using_usbmux()` and `usbmux.list_devices()`
- `supervised.py` uses `MobileConfigService` and `MobileActivationService`
- Type checkers may report missing imports — expected without the package

### Restore (restore/erase.py)
- Uses **Python API** (`Restore(...).update()`) not the broken CLI subprocess
- `_restore_with_api(ecid_int, ipsw_path, Behavior.Update/Erase)` handles all restore operations
- All three functions (`erase_device`, `update_device`, `restore_device`) require `ecid` and `ipsw` parameters
- `IRecv(ecid=int, timeout=5, is_recovery=True)` connects to Recovery/DFU devices via libusb
- `asyncio.run()` wraps the async `Restore.update()` call

---

## External Dependencies

| Binary | Used For |
|--------|----------|
| `pymobiledevice3` | Device enumeration, lockdown, restore, supervision |
| `openssl` | Mobileconfig parsing (`smime -verify`) |
| `idevicerestore` | Erase/restore flows (via `brew --prefix`) |

---

## [WARN] usbmuxd Is On-Demand — NEVER Check if it's "Running"

**usbmuxd is socket-activated.** It starts automatically when a normal-mode Apple device is plugged in and stops when no normal-mode devices are present. There is no persistent daemon to check or start.

- `/run/usbmuxd` and `/var/run/usbmuxd` socket files always exist (created by udev/systemd)
- `ps aux | grep usbmuxd` returning nothing is **normal** — it just means no normal-mode device is attached right now
- **Do NOT** add `_connect_usbmuxd()` waits or retry loops for usbmuxd
- **Do NOT** attempt to start usbmuxd manually

**Device transport modes:**
- Normal iOS mode -> usbmuxd (AF_UNIX socket at `/run/usbmuxd`)
- Recovery / DFU mode -> libusb directly (IRecv / `pymobiledevice3 restore`), usbmuxd not involved at all

---

## [WARN] Never Pass `--ecid` to `pymobiledevice3 restore update`

The `restore update` CLI accepts `--ecid` but it is **broken for Recovery mode**: the CLI passes the ecid as a raw string to `IRecv(ecid=...)`, which internally compares it against an `int`. The comparison `int != str` is always `True`, so the device is never found.

- **Do NOT** add `--ecid` to `restore update`, `restore update --erase`, etc.
- Auto-detection (no `--ecid`) finds the first Recovery-mode device via libusb

---

## Common Workflows

```bash
# Install and run
uv tool install .
ios-enroll device list

# Develop with tests
cd /var/home/dlafreniere/projects/enrollmentapp
source .venv/bin/activate
PYTHONPATH=src python -m pytest tests/ -v

# Manual testing without install
PYTHONPATH=src python -m apple_device_cli.cli device list
PYTHONPATH=src python -m apple_device_cli.cli org list

# Check current behavior
grep -r "ValueError" src/
grep -r "already exists" src/
```

---

## Anti-Patterns (What NOT To Do)

| Anti-Pattern | Why It's Wrong | What To Do |
|--------------|----------------|------------|
| Skip tests before commit | Causes regressions | Run `PYTHONPATH=src python -m pytest tests/` |
| Change error messages without updating tests | Tests assert exact strings | Update tests first |
| Assume pymobiledevice3 behavior | Library may not be installed | Read source or mock in tests |
| Check if usbmuxd is "running" | usbmuxd is on-demand, not a daemon | Let it auto-start when device plugged in |
| Pass `--ecid` to restore CLI | Broken for Recovery mode | Use Python API or auto-detect |

---

*For project methodology and workflow, see .clio/instructions.md*
