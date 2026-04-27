# Apple Device CLI - Configurator Clone

## Overview

A clean, modular CLI tool wrapping `idevice*` tools (libimobiledevice) and `pymobiledevice3` to provide Apple Configurator-like functionality on Linux.

## Architecture

```
apple_device_cli/
├── src/apple_device_cli/
│   ├── __init__.py
│   ├── cli.py              # Typer app, command definitions
│   ├── device/
│   │   ├── __init__.py
│   │   ├── connection.py   # Device connection/mux management
│   │   ├── info.py         # Device info queries
│   │   └── state.py        # Device state (normal/recovery/dfu)
│   ├── enrollment/
│   │   ├── __init__.py
│   │   ├── supervised.py   # Supervised pairing & cloud config
│   │   ├── activation.py   # Device activation
│   │   └── skip_panes.py   # Setup assistant skip logic
│   ├── restore/
│   │   ├── __init__.py
│   │   ├── erase.py        # Device erase
│   │   ├── update.py       # iOS update (fetch latest + restore)
│   │   └── ipsw.py         # IPSW handling
│   ├── orgs/
│   │   ├── __init__.py
│   │   ├── manager.py      # Org storage CRUD
│   │   └── identity.py    # Certificate/key handling
│   └── core/
│       ├── __init__.py
│       └── exceptions.py   # Custom exceptions
├── tests/
├── pyproject.toml
└── README.md
```

## Key Design Decisions

### CLI Framework
- **Typer** - Modern Python CLI framework with type hints and tab completion
- Click-based under the hood, cleaner API than raw Click

### Device Communication
- **pymobiledevice3** for async lockdown operations:
  - Supervised pairing (`pair_supervised`)
  - Mobile config service (`MobileConfigService`)
  - Device info queries
- **libimobiledevice CLI tools** for:
  - Device enumeration (`idevicepair list`)
  - Restore/erase operations (`idevicerestore`)
  - Device info fallback (`ideviceinfo`)

### Organization Storage
- Location: `~/.config/apple_device_cli/orgs/`
- Structure per org:
  ```
  ~/.config/apple_device_cli/orgs/
  ├── My Org/
  │   ├── org.json       # metadata (name, org_id, address, phone, email)
  │   ├── cert.der       # supervising certificate
  │   └── key.der        # private key
  ```

### Plugin-Friendly Design
- Each subsystem (device, enrollment, restore, orgs) is self-contained
- Clear interfaces between layers
- Easy to add new commands without touching core logic

## Command Structure

### Device Commands

| Command | Description |
|---------|-------------|
| `device list` | List connected iOS devices via usbmuxd |
| `device info --udid <UDID>` | Get device properties (UDID, name, type, build, version) |
| `device erase --udid <UDID> [--skip-esim]` | Wipe device (wipe all content and settings) |
| `device update --udid <UDID>` | Update device to latest available iOS |
| `device restore --udid <UDID> --ipsw <PATH>` | Restore device with specific IPSW |
| `device prepare --udid <UDID>` | Prepare device for enrollment (enter recovery if needed) |

### Enrollment Commands

| Command | Description |
|---------|-------------|
| `enroll --udid <UDID> --org-name <NAME>` | Full supervised enrollment workflow |
| `enroll make-supervised --udid <UDID>` | Make device supervised with org identity |
| `enroll activate --udid <UDID>` | Activate paired/supervised device |
| `enroll skip-panes [--preset <name>] [--skip <pane1> <pane2>...]` | Manage skip pane presets |

### Organization Commands

| Command | Description |
|---------|-------------|
| `org list` | List all stored organizations |
| `org create --name <NAME> [--org-id <ID>] [--address] [--phone] [--email]` | Create new organization |
| `org import --path <DIR\|ZIP\|.organization>` | Import from directory, zip, or Apple Configurator file |
| `org export --name <NAME> --path <DIR\|ZIP>` | Export organization |
| `org delete --name <NAME>` | Delete an organization |
| `org set-cert --name <NAME> -C <CERT>` | Set/update certificate |
| `org set-key --name <NAME> -K <KEY>` | Set/update private key |

## Skip Panes

Supported Setup Assistant panes to skip (from cfgutil):

- `skip-location`, `skip-restore`, `skip-sim-setup`, `skip-android`
- `skip-appleid`, `skip-intended-user`, `skip-siri`, `skip-screentime`
- `skip-diagnostics`, `skip-software-update`, `skip-passcode`, `skip-touchid`
- `skip-applepay`, `skip-zoom`, `skip-language`, `skip-region`
- `skip-true-tone`, `skip-phone-number-permission`, `skip-home-button`
- `skip-screen-saver`, `skip-tap-to-setup`, `skip-preferred-language-setup`
- `skip-keyboard-setup`, `skip-dictation-setup`, `skip-watch-migration`
- `skip-feature-highlights`, `skip-tv-provider`, `skip-tv-home-screen-sync`
- `skip-privacy`, `skip-where-is-this-apple-tv`, `skip-imessage-and-facetime`
- `skip-app-store`, `skip-safety`, `skip-multitasking`, `skip-action-button`
- `skip-apple-intelligence`, `skip-camera-controls`, `skip-terms-of-address`
- `skip-accessibility-appearance`, `skip-welcome`, `skip-appearance`
- `skip-restore-completed`, `skip-update-completed`

Presets:
- `minimal` - Only skip essential panes (restore-completed, update-completed)
- `standard` - Common enterprise setup (minimal + appleid, passcode, siri)
- `all` - Skip everything possible

## Error Handling

### Custom Exceptions
- `DeviceNotFoundError` - Device not connected or UDID not found
- `DevicePairingError` - Pairing failed
- `EnrollmentError` - Supervision enrollment failed
- `ActivationError` - Device activation failed
- `RestoreError` - Restore/erase operation failed
- `OrganizationError` - Org import/export/storage error

### Fallback Behavior
- When `idevicerestore` unavailable, suggest installation
- When `pymobiledevice3` unavailable, fallback to CLI tools where possible
- Clear error messages with recovery suggestions

## Technical Notes

### Key URLs 
- Device Activation: `https://albert.apple.com/deviceservices/deviceActivation`
- DRM Handshake: `https://albert.apple.com/deviceservices/drmHandshake`
- Firmware Builds: `https://purplerestore.apple.com/index/v5_all_builds.plist`

### Device States
- **Normal**: Device booted to iOS
- **Recovery**: Device in restore mode
- **DFU**: Device in DFU mode for restore

### Dependencies
- Python 3.8+
- Typer (CLI framework)
- pymobiledevice3 (async lockdown, supervised pairing)
- libimobiledevice (usbmuxd, idevicepair, idevicerestore, ideviceinfo)
- cryptography (certificate handling)
- construct (protocol parsing)

## Project Status

- [x] Design completed
- [ ] Implementation plan pending
- [ ] Implementation pending
