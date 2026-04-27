# AGENTS.md

Agent knowledge base for the ios-enroll project.

## Quick Reference

| Item | Value |
|------|-------|
| **Package** | `src/apple_device_cli` (Typer CLI) |
| **Entry point** | `ios-enroll = "apple_device_cli.cli:main"` |
| **Orgs dir** | `~/.config/apple_device_cli/orgs` |
| **Tests** | `python -m pytest tests/ -v` |

## Essential Commands

```bash
# Install
uv tool install .

# Run (no install)
python -m apple_device_cli.cli

# Tests
python -m pytest tests/ -v
pytest tests/test_org_manager.py -v
```

## Project Structure

```
src/apple_device_cli/
├── __init__.py          # __version__
├── cli.py               # Typer app (main entry)
├── core/exceptions.py   # AppleDeviceError, EnrollmentError
├── device/
│   ├── connection.py    # list_devices(), get_device_info()
│   └── info.py          # DeviceInfo dataclass
├── enrollment/
│   ├── activation.py    # activate_device()
│   ├── skip_panes.py    # VALID_PANES, PRESETS, resolve_skip_panes()
│   └── supervised.py    # make_supervised() via pymobiledevice3
├── orgs/
│   ├── identity.py      # generate_org_identity(), load_cert_info()
│   └── manager.py       # OrganizationManager, Organization
└── restore/
    └── erase.py         # erase_device(), update_device(), restore_device()
```

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

    # Save omits cert_path/key_path from JSON (files on disk)
    def save(self, org_dir: Path, skip_copy: bool = False)
    @classmethod def load(cls, org_dir: Path) -> Organization
```

### OrganizationManager (orgs/manager.py)
```python
class OrganizationManager:
    def __init__(self, orgs_dir: Path | None = None)  # defaults to DEFAULT_ORGS_DIR
    def list_orgs() -> list[Organization]
    def get_org(name: str) -> Organization | None
    def save_org(org: Organization)
    def delete_org(name: str) -> bool
    def import_org(path, password="password") -> Organization  # .organization, .zip, dir
    def import_mobileconfig(path) -> Organization  # uses openssl smime -verify
    def export_org(name, dest_path) -> bool
```

## Critical Behaviors & Gotchas

### Org Storage
- Each org is a directory at `~/.config/apple_device_cli/orgs/<sanitized_name>/`
- Contains: `org.json` (metadata), `cert.der` (optional), `key.der` (optional)
- **Important**: `save()` writes `org.json` but intentionally omits `cert_path`/`key_path`
- Tests depend on this layout—don't change without updating tests

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
- Type checkers may report missing imports—expected without the package

### Skip Panes (enrollment/skip_panes.py)
```python
VALID_PANES = {"location", "restore", "sim-setup", "appleid", "passcode", ...}  # 40+ items
PRESETS = {"minimal", "standard", "all"}
resolve_skip_panes(preset: str | None, extra_panes: list[str] | None) -> list[str]
```

## External Dependencies

| Binary | Used For |
|--------|----------|
| `pymobiledevice3` | Device enumeration, lockdown, restore, supervision |
| `openssl` | Mobileconfig parsing (`smime -verify`) |
| `idevicerestore` | Erase/restore flows (via `brew --prefix`) |

## Safe Modification Rules

1. **User-facing messages**: Run tests after changing; many assert exact strings
2. **Subprocess calls**: Keep stderr in error messages for test compatibility
3. **Org layout**: If changing, update `Organization.save()`/`load()` and tests
4. **pymobiledevice3 paths**: These require hardware or mocks; isolate external calls

## Testing Notes

- Tests mock `subprocess.run` for mobileconfig import
- Tests use temp directories for orgs to avoid polluting `~/.config`
- Some tests verify error message text (grep for `match=` in test files)
- Device tests likely need mocking (hardware dependent)

## Common Workflows

```bash
# Create and test a change
cd /var/mnt/Disk2/projects/enrollmentapp
source .venv/bin/activate
python -m pytest tests/ -v

# Manual testing
python -m apple_device_cli.cli device list
python -m apple_device_cli.cli org list

# Check current behavior
grep -r "ValueError" src/
grep -r "already exists" src/
```