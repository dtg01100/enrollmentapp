# iOS Supervision Enrollment Tool - Specification

## Overview

Linux CLI tool for iOS supervision enrollment, eliminating the macOS requirement of Apple Configurator.

## Interface

### CLI
```bash
ios-enroll <command>
```

Run `ios-enroll --help` for all commands, or `ios-enroll --version` for version info.

## Installation

### Manual Installation

Requires:
- Python 3.10+
- pymobiledevice3 (primary device interaction library)

## Core Components

### 1. Device Connection
- Connects to iOS devices via usbmuxd (AF_UNIX socket at `/run/usbmuxd`)
- Uses pymobiledevice3's `create_using_usbmux()` for lockdown connections
- Uses lockdown protocol (com.apple.mobile.lockdown service)
- Supports Normal, Recovery, and DFU device states

### 2. Supervision Identity
- DER-encoded certificate (-C)
- DER-encoded private key (-K)
- Organization metadata (name, address, phone, email, id)

### 3. Device States
- Normal: Device booted to iOS
- Recovery: Device in recovery mode (restore/update)
- DFU: Device Firmware Upgrade mode
- Unknown: Unrecognized or disconnected state

## Organization Management

Organizations are stored in `~/.config/apple_device_cli/orgs/` by default.

### Directory Structure
```
~/.config/apple_device_cli/orgs/
  My_Org/
    org.json      # metadata
    cert.der      # supervising certificate
    key.der       # private key
```

### Org Commands

- `org list` - List all stored organizations
- `org create --name "Name" [--org-id ID] [--address] [--phone] [--email] [-C cert] [-K key]` - Create new organization
- `org import --path <dir|zip|.organization|.mobileconfig>` - Import organization from directory, zip, Apple Configurator file, or MDM mobileconfig
- `org export --name "Name" --path <dir|zip>` - Export organization to directory or zip
- `org delete --name "Name"` - Delete an organization
- `org show --name "Name"` - Show organization details
- `org generate --name "Name"` - Generate a new supervising identity
- `org set-cert --name "Name" -C cert.der` - Set/update certificate
- `org set-key --name "Name" -K key.der` - Set/update private key
- `org set-mdm-url --name "Name" --mdm-url <URL>` - Set MDM server URL
- `org set-checkin-url --name "Name" --checkin-url <URL>` - Set SCEP check-in URL
- `org set-mdm-topic --name "Name" --mdm-topic <topic>` - Set MDM topic
- `org import-mobileconfig --path <file>` - Import from MDM .mobileconfig file
- `org set-wifi --name "Name" --wifi-config <file>` - Attach WiFi mobileconfig to org

## Device Commands

### list
List connected iOS devices via usbmuxd. Uses pymobiledevice3. Supports `--json` and `--verbose`.

### info
Get device properties (UDID, deviceName, deviceType, buildVersion, firmwareVersion). Supports `--json`.

## Enrollment Commands

### make-supervised
Apply supervision and MDM enrollment to a device:
- Requires: --udid, --org-name
- Optional: --skip-preset (minimal/standard/all), --skip (individual panes), --wifi-ssid,
  --wifi-password, --wifi-encryption, --wifi-config, --mdm-mobileconfig,
  --mdm-unremovable, --fail-on-mdm-error, --verbose

### activate
Activate a paired device.

### guided-enroll
Guided interactive enrollment workflow combining device selection, org selection,
skip panes, WiFi config, and supervised pairing.

### re-enroll
Erase device cloud config to allow fresh re-enrollment.
Optional: --force (skip confirmation prompt).

### status
Show enrollment status (activation, supervision, MDM) of a connected device.

### validate
Validate enrollment prerequisites (cert, key, MDM URL) without touching devices.

## Skip Panes

Valid panes (passed to `--skip` and grouped into `--skip-preset`):

- location, restore, sim-setup, android, appleid
- intended-user, siri, screentime, diagnostics
- software-update, passcode, touchid, apple-pay
- zoom, language, region, true-tone
- phone-number-permission, home-button, screen-saver
- tap-to-setup, preferred-language-setup, keyboard-setup
- dictation-setup, watch-migration, feature-highlights
- tv-provider, tv-home-screen-sync, privacy
- where-is-this-apple-tv, imessage-and-facetime
- app-store, safety, multitasking, action-button
- apple-intelligence, camera-controls, terms-of-address
- accessibility-appearance, welcome, appearance
- restore-completed, update-completed

Presets: `minimal`, `standard`, `all` (defined in `enrollment/skip_panes.py`).

## Technical Notes

- Uses plist protocol for lockdown communication
- Activation requires supervision identity for supervised devices
- Uses pymobiledevice3 for device communication (lockdown, mobile config, activation services)
- Supervised identity generated via `cryptography` library (self-signed cert + RSA key)
- Enables elevated operations via keybag file (PEM with cert+key)
- fcntl.flock used for cross-process org file locking

## Key Classes

| Class / Function | Location | Purpose |
|------------------|----------|---------|
| `do_supervised_pairing()` | `enrollment/supervised.py` | Core async supervision + MDM flow |
| `make_supervised()` | `enrollment/supervised.py` | Sync wrapper for supervised pairing |
| `erase_device_for_reenrollment()` | `enrollment/supervised.py` | Clear cloud config for re-enrollment |
| `get_device_enrollment_state()` | `enrollment/supervised.py` | Read device state |
| `Organization` | `orgs/manager.py` | Org metadata dataclass |
| `OrganizationManager` | `orgs/manager.py` | Org CRUD + import/export |
| `resolve_skip_panes()` | `enrollment/skip_panes.py` | Resolve presets + custom skip list |

## Usage Examples

```bash
# List connected devices
ios-enroll device list

# Create organization
ios-enroll org create --name "My Org" --org-id "com.example" -C cert.der -K key.der

# List organizations
ios-enroll org list

# Show organization details
ios-enroll org show --name "My Org"

# Export organization
ios-enroll org export --name "My Org" --path ./my_org.zip

# Import organization (Apple Configurator .organization file)
ios-enroll org import --path "Example Organization.organization"

# Import from MDM mobileconfig
ios-enroll org import --path profile.mobileconfig

# Get device info
ios-enroll device info --udid <UDID>

# Guided interactive enrollment
ios-enroll enroll guided-enroll

# Make device supervised
ios-enroll enroll make-supervised --udid <UDID> --org-name "My Org"

# Activate device
ios-enroll enroll activate --udid <UDID>

# Check version
ios-enroll --version
```

## Project Structure

```
enrollmentapp/
├── pyproject.toml          # Package config (ios-enroll, hatchling)
├── README.md               # Quick start guide
├── SPEC.md                 # This specification
├── src/apple_device_cli/   # Primary package
│   ├── __init__.py         # Version
│   ├── cli.py              # Typer CLI entrypoint
│   ├── core/               # Exceptions, utilities
│   ├── device/             # Device connection, info
│   ├── enrollment/         # Supervised pairing, activation
│   └── orgs/               # Organization management (manager, identity)
├── tests/                  # pytest test suite
├── docs/                   # Project documentation
│   ├── ENROLLMENT_FLOWS.md # Enrollment flow architecture
│   └── INSTALL_WINDOWS.md  # Windows install + GUI launch guide
├── CHANGELOG.md            # Release history
├── AGENTS.md               # Developer reference
├── build.sh                # Linux native build (Nuitka)
├── build_nuitka.py         # Cross-platform build entrypoint (used by build.sh + build_windows.bat)
├── build_windows.bat       # Windows native build (Nuitka + MSVC)
```
