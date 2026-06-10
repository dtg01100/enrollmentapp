# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Bug Fixes
- **Re-enrollment exit code**: `enroll re-enroll` now prints error message and exits with code 1 on failure
- **Keybag cleanup**: Sensitive keybag files are now deleted after enrollment completes (ensured via `finally` block)
- **Profile list iteration**: Robust handling of both dict and list formats from pymobiledevice3
- **Re-enrollment polling**: Device reconnection now polled instead of fixed 30-second sleep
- **State rename**: `is_mdm_managed` renamed to `was_mandatorily_unpaired` for accuracy
- **Dead code removal**: Unused `enrollment/flows.py` and associated test files deleted

### Changed
- **fcntl locking**: `save_org()` and `import_mobileconfig()` now use cross-process `fcntl.flock` to prevent races
- **Keybag cleanup**: Extracted `_cleanup_keybag()` helper, wrapped body in `try/finally` to guarantee cleanup
- **TemporaryDirectory**: Removed empty wrapper around supervised pairing body

### Docs
- **Activation state string**: Documented that `"Unactivated"` is a shared pymobiledevice3 convention
- **Skip pane mapping**: Documented `apple-pay` → `Payment` mapping per Apple's `skipkeys.yaml`
- **Architecture**: Noted `flows.py` deletion in module docs

### Testing
- **Coverage**: Added tests for lock acquisition on import, keybag helper in isolation, concurrent save+import contention, cert-load exception cleanup, and re-enroll success path
- **Test reliability**: Fixed timing-window flakiness in concurrent lock test using `threading.Event`

## v1.0.0 (2026-05-27)

### Bug Fixes
- **Cloud config reuse**: Devices with existing matching cloud config are now treated as success rather than failure
- **MDM install retry**: Silent MDM profile install now retries up to 3 times on transient network errors
- **Error message formatting**: Simplified, human-readable error messages for mobileconfig failures
- **Quoted path handling**: WiFi mobileconfig paths entered with quotes are now normalized before use
- **Status readback**: Device enrollment state now correctly reads lockdown keys and cloud configuration
- **Pairing regression**: Fixed supervision pairing early-return and flow return types
- **Reconnect timing**: Adjusted device reconnection timeout and error propagation
- **Skip panes**: Added `appleid` to the `minimal` preset

### Privacy
- **Output redaction**: All user-facing CLI output is sanitized to prevent accidental exposure of PII/secrets

### Technical
- Built on `pymobiledevice3`
- Organization storage in `~/.config/apple_device_cli/orgs/`
- Comprehensive test suite with unit, integration, and redaction coverage

## v1.0.0b (2026-05-20) - Beta release

### Features
- **CLI**: Typer-based `ios-enroll` command for device management
- **Organizations**: Create, delete, show, import, export with PKCS12 identity support
- **Device operations**: list, info
- **Enrollment**: supervised pairing, activation, guided-enroll, re-enroll, status, validate
- **Skip panes**: Presets for Setup Assistant configuration (66 panes supported)
- **Import**: Apple Configurator `.organization` and MDM `.mobileconfig` files
- **Identity**: Self-signed CA and server certificate generation
- **WiFi configuration**: Headless enrollment via WiFi mobileconfig
- **Linux support**: USB/udev rules for Apple devices

## v0.1.0 (2026-04-27)

### Features
- **CLI**: Typer-based `ios-enroll` command for device management
- **Organizations**: Create, delete, show, import, export with PKCS12 identity support
- **Device operations**: list, info, erase, restore, update
- **Enrollment**: supervised pairing, activation, guided-enroll
- **Skip panes**: Presets for Setup Assistant configuration (66 panes supported)
- **Import**: Apple Configurator `.organization` and MDM `.mobileconfig` files
- **Identity**: Self-signed CA and server certificate generation
- **WiFi configuration**: Headless enrollment via WiFi mobileconfig
- **Linux support**: USB/udev rules for Apple devices (normal, recovery, DFU modes)

### Bug Fixes
- **Cloud config reuse**: Devices with existing matching cloud config are now treated as success rather than failure, eliminating spurious "Failed to re-configure" errors
- **MDM install retry**: Silent MDM profile install now retries up to 3 times on transient network/offline errors (5-second backoff)
- **Error message formatting**: Simplified, human-readable error messages for mobileconfig failures instead of raw payload dumps
- **Quoted path handling**: WiFi mobileconfig paths entered with quotes are now normalized before use
- **Status readback**: Device enrollment state now correctly reads lockdown keys and cloud configuration

### Privacy
- **Output redaction**: All user-facing CLI output, progress messages, and error texts are sanitized to prevent accidental exposure of:
  - Organization names, IDs, and topics
  - Device UDIDs (shown as first 8 hex chars only)
  - File paths (home directories truncated to `~/…/`)
  - Email addresses, phone numbers, physical addresses
  - MDM URLs (scheme/host preserved, path truncated)
  - Long hex tokens and UUIDs
- Supported home-like path layouts: `/var/home/`, `/home/`, `/Users/`, and custom-mounted user directories

### Technical
- Built on `pymobiledevice3` and `libimobiledevice`
- Organization storage in `~/.config/apple_device_cli/orgs/`
- Comprehensive test suite with unit, integration, and redaction coverage

