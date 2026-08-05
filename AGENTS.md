# ios-enroll AGENTS.md

**Purpose:** Technical reference for ios-enroll project development

---

## Project Overview

**ios-enroll** is an iOS device supervised enrollment CLI for Linux — an Apple Configurator alternative.

- **Language:** Python 3.10+
- **Architecture:** Typer CLI application
- **Package:** `src/apple_device_cli`
- **Entry point:** `ios-enroll = "apple_device_cli.cli:app"`

---

## Quick Setup

```bash
# Install
uv tool install .

# Run (no install)
python -m apple_device_cli.cli

# Test (PYTHONPATH optional — pyproject.toml sets pythonpath = ["src"])
PYTHONPATH=src python -m pytest tests/ -v
```

### Optional Extras

The project has two optional dependency groups in `pyproject.toml`:

```bash
# PySide6 GUI (ios-enroll-gui, --gui flag)
pip install '.[gui]'

# Native onefile builds via Nuitka
pip install '.[build]'
```

Both are optional at runtime — `ios-enroll` works without them.

---

## Architecture

```
User Input (CLI)
    |
    v
Typer App (cli.py)
    |
    +-- device/      # Device enumeration, info
    +-- enrollment/  # Supervised pairing, activation, skip panes
    +-- orgs/        # Organization management, identity
    +-- core/        # Exceptions, redaction utilities
```

Note: `enrollment/flows.py` was removed in 1.0.0b-post as dead code (not imported by cli.py or any other production module).

---

## Directory Structure

| Path | Purpose |
|------|---------|
| `src/apple_device_cli/` | Main package |
| `src/apple_device_cli/cli.py` | Typer app entry point |
| `src/apple_device_cli/core/` | Exceptions, redaction |
| `src/apple_device_cli/device/` | Device connection, info |
| `src/apple_device_cli/enrollment/` | Supervised, activation, skip panes |
| `src/apple_device_cli/orgs/` | Organization manager, identity |
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
| `orgs/manager.py` | OrganizationManager, Organization |
| `orgs/identity.py` | generate_org_identity(), load_cert_info() |
| `core/exceptions.py` | AppleDeviceError, EnrollmentError |
| `core/redaction.py` | Address, email, identifier redaction |

---

## Testing

**Before Committing:**

```bash
# Run all tests
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
    wifi_config_path, mdm_mobileconfig_path: str | None
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
    def read_wifi_profile(name: str) -> dict | None:  # NEW since 1.1.0
        """Read SSID/password/encryption from the org's wifi.mobileconfig.
        Returns None if no wifi_config_path or file is unreadable."""
    def import_mobileconfig(self, path: str | Path) -> Organization
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

### WiFi Configuration
- CLI auto-detects org's `wifi_config_path` and offers to include it during enrollment
- User is prompted with default Yes if org has known WiFi config
- CLI options (`--wifi-config`, `--wifi-ssid`) take priority over org's config
- WiFi is installed **before** MDM profile (Step 5 in enrollment flow)

### MDM Profile Installation (regression risk)
- **Use `install_profile_silent(keybag_path, payload_bytes)`** after WiFi is installed — device can reach MDM server
- Requires escalation via the org's keybag file (created from cert/key in Step 3)
- **Fallback**: Use `store_profile(payload_bytes, Purpose.PostSetupInstallation)` if no keybag available
- **Critical ordering**: Cloud config → WiFi → MDM profile with escalation (Steps 3 → 5 → 6)
- **Warning**: `store_profile` alone only works if device goes through Setup Assistant after enrollment
- Retry logic: 3 attempts with 5s backoff for transient network errors

### Activation State String Comparison

- `if activation_state == "Unactivated":` at `enrollment/supervised.py:525`
  looks like a magic string (no constant)
- Matches pymobiledevice3's own idiom in
  `MobileActivationService.activate()`
  (`pymobiledevice3/services/mobile_activation.py:105`)
- `"Unactivated"` is not in the library's public API — replacing it
  locally would just create a redundant re-export
- **Do not "fix"** — treat it as a stable shared convention

### Skip Pane Mapping Exceptions

- `SKIP_SETUP_MAPPING` in `enrollment/supervised.py:39` maps user-facing
  pane names to lockdown key values
- Most look obvious (`"location"` → `"Location"`), but a few are
  intentionally non-obvious
- `"apple-pay"` → `"Payment"` — the iOS 8.1+ lockdown key for "Skips
  Apple Pay setup" is named `Payment`, not `ApplePay`. Source: Apple's
  [`device-management/skipkeys.yaml`](https://github.com/apple/device-management/blob/main/skipkeys.yaml)
- All 62 entries were cross-checked against pymobiledevice3's
  `MobileConfigService.supervise()` default skip list
  (`services/mobile_config.py:201-283`)
- When adding new entries, consult that list as the source of truth

---

## External Dependencies

| Dependency | Used For |
|------------|----------|
| `pymobiledevice3` | Device enumeration, lockdown, supervision |
| `cryptography` | Certificate/key generation, PKCS12 loading |
| `openssl` | Mobileconfig parsing (`smime -verify`) |
| `typer` | CLI framework |
| `rich` | Terminal output formatting |

---

## [WARN] usbmuxd Is On-Demand — NEVER Check if it's "Running"

**usbmuxd is socket-activated.** It starts automatically when a normal-mode Apple device is plugged in and stops when no normal-mode devices are present. There is no persistent daemon to check or start.

- `/run/usbmuxd` and `/var/run/usbmuxd` socket files always exist (created by udev/systemd)
- `ps aux | grep usbmuxd` returning nothing is **normal** — it just means no normal-mode device is attached right now
- **Do NOT** add `_connect_usbmuxd()` waits or retry loops for usbmuxd
- **Do NOT** attempt to start usbmuxd manually

**Device transport modes:**
- Normal iOS mode -> usbmuxd (AF_UNIX socket at `/run/usbmuxd`)

---

## Common Workflows

```bash
# Install and run
uv tool install .
ios-enroll device list

# Develop with tests
cd /var/mnt/Disk2/projects/enrollmentapp
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
|--------------|----------------|--------------|
| Skip tests before commit | Causes regressions | Run `PYTHONPATH=src python -m pytest tests/` |
| Change error messages without updating tests | Tests assert exact strings | Update tests first |
| Assume pymobiledevice3 behavior | Library may not be installed | Read source or mock in tests |
| Check if usbmuxd is "running" | usbmuxd is on-demand, not a daemon | Let it auto-start when device plugged in |
| `MagicMock()` (no `spec=`) for any class-shaped value | Typos and dead-code references silently return mocks; tests "pass" while production breaks | `MagicMock(spec=RealClass)`. See "Mock Spec'ing" below |

### Mock Spec'ing (Required)

Bare `MagicMock()` is forbidden in this project. Every class-shaped mock
**must** be spec'd against the real class:

```python
# WRONG — typos and dead-code references return mocks silently
lockdown = MagicMock()
mock_manager = MagicMock()
result = MagicMock()

# RIGHT — attribute access and method signatures are type-checked
lockdown = MagicMock(spec=LockdownClient)
mock_manager = MagicMock(spec=OrganizationManager)
result = MagicMock(spec=CompletedProcess)
```

**Why it matters:** A spec'd mock raises `AttributeError` on unknown
attribute reads, and raises `TypeError` on method calls with wrong
arguments. This is the *only* way the test can detect that production
code has misspelled a name or removed a field. A bare mock returns
a mock for *any* attribute, so the test "passes" while the production
code is broken.

**What about the spec class itself?** Spec classes (`LockdownClient`,
`MobileActivationService`, `MobileConfigService`) come from
`pymobiledevice3`, and the autouse `mock_pymobiledevice3` fixture in
`tests/conftest.py` patches pymobiledevice3 in `sys.modules` with
spec'd mocks at test runtime. So `from pymobiledevice3.lockdown
import LockdownClient` inside a test would resolve to a Mock, and
`MagicMock(spec=Mock)` raises `InvalidSpecError`. Test files must
import the real classes from `tests.conftest` instead — see the
`__all__` there.

**Default test run:** `grep "MagicMock()" tests/ --include="*.py" \| grep -v "spec=" \| grep -v conftest.py` should return **zero** lines. If you add a mock, spec it.

### CLI Help Messages

Incomplete commands show helpful guidance instead of errors:
- `ios-enroll` alone → shows main commands
- `ios-enroll device` → shows device subcommands
- `ios-enroll org` → shows org subcommands
- `ios-enroll enroll` → shows enrollment subcommands

Each subapp (`device_app`, `org_app`, `enroll_app`) has `invoke_without_command=True` and a callback that displays available commands. Do not remove these — they improve UX for incomplete commands.

---

*For project methodology and workflow, see .clio/instructions.md*
