# Release Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish ios-enroll v0.1.0 for internal/shareable release — commit pending changes, clean repo, update docs, add license/changelog, fix BER warning, tag release.

**Architecture:** Sequential cleanup tasks with no code changes beyond one bug fix (PKCS#7 warning suppression). Each task produces a single focused commit.

**Tech Stack:** Python 3.8+, hatchling, typer, cryptography, pymobiledevice3

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `.gitignore` | Expanded ignore patterns for junk/artifacts |
| Delete | `Apple Configurator.app/` | macOS binary reference, not needed |
| Delete | `enroll_gui.py` | Broken — references deleted enroll.py, gradio not a dep |
| Create | `LICENSE` | MIT license text |
| Modify | `README.md` | Accurate CLI name, commands, paths |
| Modify | `SPEC.md` | Remove stale references, update structure |
| Modify | `src/apple_device_cli/orgs/manager.py:256-259` | Suppress PKCS#7 BER warning |
| Create | `CHANGELOG.md` | Version history stub |

---

### Task 1: Commit current working changes

Stage all modified and deleted files (source + tests) as a single commit. Untracked junk files are NOT included — they'll be gitignored in Task 2.

**Files:**
- Modify: (staged by git) `src/apple_device_cli/cli.py`, `src/apple_device_cli/orgs/manager.py`, `src/apple_device_cli/device/connection.py`, `src/apple_device_cli/device/state.py`, `src/apple_device_cli/enrollment/activation.py`, `src/apple_device_cli/enrollment/supervised.py`, `src/apple_device_cli/orgs/identity.py`, `tests/test_enrollment.py`, `tests/test_restore.py`, `uv.lock`
- Delete: (staged by git) `enroll.py`, `src/apple_device_cli/enrollment/skip_panes.py`, `src/apple_device_cli/restore/ipsw.py`, `src/apple_device_cli/restore/update.py`

- [ ] **Step 1: Stage all modified and deleted files**
```bash
git add -u
```

- [ ] **Step 2: Verify what's staged**
```bash
git diff --cached --stat
```
Expected: Shows all modified/deleted files listed above, no untracked junk files.

- [ ] **Step 3: Commit**
```bash
git commit -m "feat: cli improvements, org identity, and cleanup legacy files"
```

- [ ] **Step 4: Verify clean state for tracked files**
```bash
git status --short
```
Expected: Only untracked files remaining (junk artifacts, AGENTS.md, etc.)

---

### Task 2: Expand .gitignore

Add patterns for all junk artifacts, build artifacts, editor/tool dirs, and credential files.

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Write the updated .gitignore**

Replace the entire file with:

```
.worktrees/
__pycache__/
*.pyc

.venv/
build/
dist/
*.egg-info/

.pytest_cache/
.ruff_cache/
.mypy_cache/

.opencode/
.crush/

*.ipsw
*.ipsw.lock
restore_*.log

*.der
*.pem
*.mobileconfig
*.organization

Apple Configurator.app/

docs/superpowers/
```

- [ ] **Step 2: Verify gitignore works**
```bash
git status --short
```
Expected: Previously untracked junk files (*.ipsw, *.der, *.pem, *.mobileconfig, *.organization, restore_*.log, Apple Configurator.app/, build/, docs/superpowers/, .opencode/, .crush/, .pytest_cache/, .ruff_cache/, .mypy_cache/) no longer appear. Only `AGENTS.md`, `enroll_gui.py`, `LICENSE`, `CHANGELOG.md` (if created), and `homebrew/` should still show if untracked.

- [ ] **Step 3: Commit**
```bash
git add .gitignore
git commit -m "chore: expand .gitignore for build artifacts and junk files"
```

---

### Task 3: Delete Apple Configurator.app/

Remove the macOS binary reference directory from the repo.

**Files:**
- Delete: `Apple Configurator.app/`

- [ ] **Step 1: Remove the directory**
```bash
rm -rf "Apple Configurator.app"
```

- [ ] **Step 2: Verify removal**
```bash
ls "Apple Configurator.app" 2>&1
```
Expected: "No such file or directory" (this dir was never committed, so no git staging needed — it's already gitignored by Task 2)

Note: Since `Apple Configurator.app/` was never committed (shows as `??` in git status) and is now gitignored, no git add/commit needed. It's already handled.

---

### Task 4: Add LICENSE file

Add the MIT license as stated in README.

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Create LICENSE file**

Write the following content to `LICENSE`:

```
MIT License

Copyright (c) 2026 ios-enroll contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Commit**
```bash
git add LICENSE
git commit -m "chore: add MIT license"
```

---

### Task 5: Update README.md

Fix CLI name from `apple-device` to `ios-enroll`, add missing commands, fix org path, update requirements.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write updated README.md**

Replace the entire file with:

```markdown
# ios-enroll

iOS device supervised enrollment CLI for Linux — an Apple Configurator alternative.

## Installation

```bash
uv tool install .
```

## Usage

### Device Commands

```bash
ios-enroll device list                           # List connected devices
ios-enroll device info [--udid <UDID>]           # Get device info
ios-enroll device erase --udid <UDID>            # Erase device
ios-enroll device update --udid <UDID>           # Update device to latest iOS
ios-enroll device restore --udid <UDID> --ipsw <PATH>  # Restore with IPSW
```

### Organization Commands

```bash
ios-enroll org list                              # List organizations
ios-enroll org create --name "My Org"            # Create organization
ios-enroll org delete --name "My Org"            # Delete organization
ios-enroll org show --name "My Org"              # Show organization details
ios-enroll org import --path <file|dir|zip>      # Import from .organization, dir, or zip
ios-enroll org export --name "My Org" --path <dir|zip>  # Export organization
ios-enroll org generate --name "My Org"          # Generate supervising identity
ios-enroll org set-cert --name "My Org" -C cert.der    # Set certificate
ios-enroll org set-key --name "My Org" -K key.der      # Set private key
ios-enroll org set-mdm-url --name "My Org" --mdm-url <URL>  # Set MDM URL
```

### Enrollment Commands

```bash
ios-enroll enroll guided-enroll                  # Guided interactive enrollment
ios-enroll enroll make-supervised --udid <UDID> --org-name "My Org"  # Make supervised
ios-enroll enroll activate --udid <UDID>         # Activate device
```

### Other

```bash
ios-enroll version                               # Show version
```

## Organization Storage

Organizations are stored in `~/.config/apple_device_cli/orgs/` by default. Each org directory contains `org.json` and optionally `cert.der` and `key.der`.

## Requirements

- Python 3.8+
- pymobiledevice3 (primary device interaction library)
- libimobiledevice (idevicepair, ideviceinfo — for basic device enumeration)

## License

MIT
```

- [ ] **Step 2: Commit**
```bash
git add README.md
git commit -m "docs: update README with correct CLI name and commands"
```

---

### Task 6: Update SPEC.md

Remove stale references to enroll.py, enroll_gui.py, old paths, and old project structure.

**Files:**
- Modify: `SPEC.md`

- [ ] **Step 1: Write updated SPEC.md**

Replace the entire file with:

```markdown
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
  org.json   # metadata
  cert.der   # supervising certificate
  key.der    # private key
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
ios-enroll org import --path "Example Organization.organization"

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
├── pyproject.toml              # Package config (ios-enroll, hatchling)
├── README.md                   # Quick start guide
├── SPEC.md                     # This specification
├── src/apple_device_cli/       # Primary package
│   ├── __init__.py             # Version
│   ├── cli.py                  # Typer CLI entrypoint
│   ├── core/                   # Exceptions, utilities
│   ├── device/                 # Device connection, info, state
│   ├── enrollment/             # Supervised pairing, activation
│   ├── orgs/                   # Organization management (manager, identity)
│   └── restore/                # Erase, restore helpers
├── tests/                      # pytest test suite
├── homebrew/                   # Homebrew installation scripts
│   ├── install.sh
│   └── Library/Taps/local/enrollment/
│       └── Formula/
└── scripts/                    # Utility scripts
```
```

- [ ] **Step 2: Commit**
```bash
git add SPEC.md
git commit -m "docs: update SPEC.md with current CLI, paths, and structure"
```

---

### Task 7: Fix PKCS#7 BER warning in import_mobileconfig

The `pkcs7.load_der_pkcs7_certificates()` call at line 257 of `manager.py` emits a `UserWarning: PKCS7 BER` warning from cryptography. The `_import_from_organization` method already suppresses this at line 167-168 using `warnings.catch_warnings()`. Apply the same pattern.

**Files:**
- Modify: `src/apple_device_cli/orgs/manager.py:255-259`

- [ ] **Step 1: Apply the warnings suppression**

Replace lines 255-259:

```python
        # Extract certificates from PKCS7 structure
        try:
            pkcs7_certs = pkcs7.load_der_pkcs7_certificates(data)
        except Exception:
            pkcs7_certs = []
```

With:

```python
        # Extract certificates from PKCS7 structure
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pkcs7_certs = pkcs7.load_der_pkcs7_certificates(data)
        except Exception:
            pkcs7_certs = []
```

- [ ] **Step 2: Run tests to verify no regressions**
```bash
python -m pytest tests/ -v
```
Expected: All tests pass (same 28 tests as before).

- [ ] **Step 3: Commit**
```bash
git add src/apple_device_cli/orgs/manager.py
git commit -m "fix: suppress PKCS7 BER warning in import_mobileconfig"
```

---

### Task 8: Delete enroll_gui.py

Remove the broken GUI script that references the deleted `enroll.py` and uses Gradio (not a project dependency).

**Files:**
- Delete: `enroll_gui.py`

- [ ] **Step 1: Delete the file**
```bash
rm enroll_gui.py
```

- [ ] **Step 2: Commit**
```bash
git add enroll_gui.py
git commit -m "chore: remove broken enroll_gui.py (references deleted enroll.py)"
```

---

### Task 9: Add CHANGELOG.md

Create a version history stub for v0.1.0.

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Create CHANGELOG.md**

Write the following content:

```markdown
# Changelog

## v0.1.0 (2026-04-23)

Initial internal release.

### Features
- iOS device management: list, info, erase, update, restore
- Organization management: create, delete, show, import, export, generate, set-cert, set-key, set-mdm-url
- Supervised enrollment: make-supervised, activate, guided-enroll
- Apple Configurator .organization import with PKCS12 identity extraction
- MDM .mobileconfig import with PKCS7 certificate extraction
- Supervising identity generation (self-signed CA + server cert)
- Skip panes presets for Setup Assistant configuration
```

- [ ] **Step 2: Commit**
```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md for v0.1.0"
```

---

### Task 10: Tag v0.1.0

Create an annotated git tag for the release.

- [ ] **Step 1: Verify clean working tree**
```bash
git status --short
```
Expected: Empty output (no uncommitted changes). Only untracked files that are gitignored should remain.

- [ ] **Step 2: Create annotated tag**
```bash
git tag -a v0.1.0 -m "v0.1.0: Initial internal release"
```

- [ ] **Step 3: Verify tag**
```bash
git tag -l
git log --oneline -1
```
Expected: Shows `v0.1.0` in tag list, and the latest commit has the tag.
