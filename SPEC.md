# iOS Supervision Enrollment Tool - Specification

## Overview

Linux CLI tool for iOS supervision enrollment, eliminating the macOS requirement of Apple Configurator.

## Interface

### CLI
```bash
ios-enroll <command>
```

Run `ios-enroll --help` for all commands, or `ios-enroll version` for version info.

## Installation

### Homebrew Installation (Recommended for Linux)

```bash
# Install all dependencies and this tool
./homebrew/install.sh

# Or manually:
brew install libimobiledevice
```

### Manual Installation

Requires:
- Python 3.8+
- pymobiledevice3 (primary device interaction library)
- libimobiledevice (for basic device communication)

## Key URLs 

- Device Activation: `https://albert.apple.com/deviceservices/deviceActivation`
- DRM Handshake: `https://albert.apple.com/deviceservices/drmHandshake`
- Firmware Builds: `https://purplerestore.apple.com/index/v5_all_builds.plist`

## Core Components

### 1. MobileDeviceConnection
- Connects to iOS devices via usbmuxd
- Uses lockdown protocol (com.apple.mobile.lockdown service)
- Handles plist-based message exchange

### 2. Supervision Identity
- DER-encoded certificate (-C)
- DER-encoded private key (-K)
- Organization metadata (name, address, phone, email, id)

### 3. Device States
- Normal: Device booted to iOS
- Recovery: Device in restore mode
- DFU: Device in DFU mode for restore

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

## Device Commands

### list
List connected iOS devices via usbmuxd. Uses pymobiledevice3 and libimobiledevice.

### info
Get device properties (UDID, deviceName, deviceType, buildVersion, firmwareVersion).

### erase
Erase device (wipe all content and settings):
- Requires: --udid
- Optional: --skip-esim (skip erasing embedded eSIMs)
- Uses pymobiledevice3 for restore operations

### update
Update device to latest available iOS version:
- Requires: --udid
- Optional: --skip-software-update, --skip-update-completed
- Fetches latest build from purplerestore.apple.com

### restore
Restore IPSW on device:
- Requires: --udid, --ipsw
- Optional: --skip-restore-completed, --skip-update-completed
- Uses pymobiledevice3 for restore operations

### make-supervised
Make device supervised using certificate:
- Requires: --org-name
- Optional: --skip-preset, --skip, --wifi-ssid, --wifi-password, --wifi-encryption

### activate
Activate paired device using albert.apple.com/deviceActivation.

### guided-enroll
Guided interactive enrollment workflow combining device selection, org selection, skip panes, erase, and supervised pairing.

## Skip Panes (from cfgutil strings)

- skip-location
- skip-restore
- skip-sim-setup
- skip-android
- skip-appleid
- skip-intended-user
- skip-siri
- skip-screentime
- skip-diagnostics
- skip-software-update
- skip-passcode
- skip-touchid
- skip-applepay
- skip-zoom
- skip-language
- skip-region
- skip-true-tone
- skip-phone-number-permission
- skip-home-button
- skip-screen-saver
- skip-tap-to-setup
- skip-preferred-language-setup
- skip-keyboard-setup
- skip-dictation-setup
- skip-watch-migration
- skip-feature-highlights
- skip-tv-provider
- skip-tv-home-screen-sync
- skip-privacy
- skip-where-is-this-apple-tv
- skip-imessage-and-facetime
- skip-app-store
- skip-safety
- skip-multitasking
- skip-action-button
- skip-apple-intelligence
- skip-camera-controls
- skip-terms-of-address
- skip-accessibility-appearance
- skip-welcome
- skip-appearance
- skip-restore-completed
- skip-update-completed

## Technical Notes

- Uses plist protocol for lockdown communication
- SRP (Secure Remote Password) authentication for some operations
- Device must be erased before restore
- Activation requires supervision identity for supervised devices
- Uses pymobiledevice3 for device communication and restore operations
- Uses libimobiledevice for basic device enumeration (idevicepair, ideviceinfo)

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
ios-enroll org import --path "Capital Candy Company Inc.organization"

# Import from MDM mobileconfig
ios-enroll org import --path profile.mobileconfig

# Get device info
ios-enroll device info --udid <UDID>

# Erase device (wipe)
ios-enroll device erase --udid <UDID>

# Guided interactive enrollment
ios-enroll enroll guided-enroll

# Make device supervised
ios-enroll enroll make-supervised --udid <UDID> --org-name "My Org"

# Activate device
ios-enroll enroll activate --udid <UDID>

# Check version
ios-enroll version
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
│   ├── device/             # Device connection, info, state
│   ├── enrollment/         # Supervised pairing, activation
│   ├── orgs/               # Organization management (manager, identity)
│   └── restore/            # Erase, restore helpers
├── tests/                  # pytest test suite
├── homebrew/               # Homebrew installation scripts
│   ├── install.sh
│   └── Library/Taps/local/enrollment/
│       └── Formula/
└── scripts/                # Utility scripts
```
