# iOS Supervision Enrollment Tool - Specification

## Overview

Linux CLI tool (and optional Gradio web GUI) for iOS supervision enrollment, eliminating the macOS requirement of Apple Configurator.

## Interface Options

### CLI
```bash
./enroll.py <command>
```

### GUI
```bash
./enroll.py gui
```
Launches a Gradio web application in browser at http://127.0.0.1:7860 with:
- Organization panel: select, view details, import
- Device panel: refresh and select
- Tabs: Erase, Update, Make Supervised, Restore, Info, Full Enrollment

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
- libimobiledevice (for device communication)
- idevicerestore (for restore/erase operations)
- Gradio (for web GUI)

## Key URLs (Discovered from reverse engineering)

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

Organizations are stored in `~/.config/enrollment/orgs/` by default.

### Directory Structure
```
~/.config/enrollment/orgs/
  My Org/
    org.json       # metadata
    cert.der       # supervising certificate
    key.der        # private key
```

### Org Commands

- `org list` - List all stored organizations
- `org create --name "Name" [--org-id ID] [--address] [--phone] [--email] [-C cert] [-K key]` - Create new organization
- `org import --path <dir|zip|.organization>` - Import organization from directory or zip or Apple Configurator file
- `org export --name "Name" --path <dir|zip>` - Export organization to directory or zip
- `org delete --name "Name"` - Delete an organization
- `org set-cert --name "Name" -C cert.der` - Set/update certificate
- `org set-key --name "Name" -K key.der` - Set/update private key

## Device Commands

### list
List connected iOS devices via usbmuxd. Uses libimobiledevice.

### info
Get device properties (UDID, deviceName, deviceType, buildVersion, firmwareVersion).

### erase
Erase device (wipe all content and settings):
- Requires: --udid
- Optional: --skip-esim (skip erasing embedded eSIMs)
- Uses idevicerestore when available

### update
Update device to latest available iOS version:
- Requires: --udid
- Optional: --skip-software-update, --skip-update-completed
- Fetches latest build from purplerestore.apple.com

### restore
Restore IPSW on device:
- Requires: --udid, --ipsw
- Optional: --skip-restore-completed, --skip-update-completed
- Uses idevicerestore when available

### prepare
Prepare device for enrollment:
- Connects to device in recovery mode
- Applies skip-xxx options to Setup Assistant
- Triggers restore with IPSW
- Options: --skip-restore-completed, --skip-update-completed, --skip-all, --skip-passcode, --skip-language, --skip-region

### make-supervised
Make device supervised using certificate:
- Requires: -C (cert), -K (key), --org-name
- Optional: --forbid-itunes-pairing, --forbid-mac-pairing, --org-id, --org-address, --org-phone, --org-email

### activate
Activate paired device using albert.apple.com/deviceActivation.

### enroll
Complete supervision enrollment workflow combining prepare, make-supervised, activate.

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
- Uses libimobiledevice for device communication
- Uses idevicerestore for restore/erase operations (not yet fully integrated)

## Usage Examples

```bash
# List connected devices
./enroll.py list

# Create organization
./enroll.py org create --name "My Org" --org-id "com.example" -C cert.der -K key.der

# List organizations
./enroll.py org list

# Export organization
./enroll.py org export --name "My Org" --path ./my_org.zip

# Import organization (Apple Configurator .organization file)
./enroll.py org import --path "Capital Candy Company Inc.organization"

# Get device info
./enroll.py info --udid <UDID>

# Erase device (wipe)
./enroll.py erase --udid <UDID>
./enroll.py erase --udid <UDID> --skip-esim

# Update device to latest iOS
./enroll.py update --udid <UDID>

# Launch GUI
./enroll.py gui
```

## Project Structure

```
enrollmentapp/
├── enroll.py           # Main CLI tool
├── enroll_gui.py       # Gradio web GUI
├── SPEC.md             # This specification
├── homebrew/
│   ├── install.sh      # Homebrew installation script
│   └── Library/Taps/local/enrollment/
│       └── Formula/    # Homebrew formulas
└── Apple Configurator.app/  # (Reference only - macOS binary)
```
