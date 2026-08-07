# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-08-07

### Added
- `ios-enroll device restore` CLI subcommand with `--udid`, `--ipsw`,
  `--list-versions`, `--cache-dir`, `--show-cache`, `--clear-cache`.
  Backed by `idevicerestore` (primary) with a `pymobiledevice3` fallback
  stub (known brittle on iOS 26; the primary path is the one to use).
- Restore tab in `ios-enroll-gui` next to Devices / Organizations /
  Enrollment. Device picker → signed-version dropdown (populated from
  `ipsw --urls`) → Start button. Or "Browse for .ipsw" for an explicit
  local file.
- Configurable firmware cache directory with 4-tier precedence:
  `--cache-dir` flag > `IOS_ENROLL_CACHE_DIR` env >
  `~/.config/ios-enroll/config.json` field `cache_dir` >
  `~/.cache/ios-enroll/firmware/`. The default is NOT `/tmp` (tmpfs
  quota on this host is ~12.5 GB; OOM risk on large IPSW downloads).
- Resume-on-partial support for IPSW downloads via HTTP `Range:`.

### Notes
- The restore subprocess has no timeout — older iPads can run 45-60+
  minutes for a full restore. Run the CLI in a `tmux`/`screen` window
  or via the agent's `background=true, notify_on_complete=true` mode
  to survive the agent's 600s foreground terminal timeout.
- Only the existing pair-on-failure wrapper (commit `a78b62a`) auto-
  recovers. Other `idevicerestore` non-zero exit codes surface
  immediately — looping on the same failure wastes 30+ minutes.

## [1.1.0] - 2026-08-05

### Added
- **PySide6 GUI**: New optional graphical interface via the `[gui]` extra (`pip install 'ios-enroll[gui]'`).
  Launch with `ios-enroll --gui` or the standalone `ios-enroll-gui` script. Provides interactive
  device list, organization picker, enrollment form, and connection dialogs. Backed by
  `apple_device_cli.gui_qt` with PySide6 imported lazily so headless installs still work.
- **GUI enrollment form**: WiFi SSID/password/encryption auto-populated from selected org's
  `wifi.mobileconfig` via the new `OrganizationManager.read_wifi_profile()` helper.
- **`--gui` CLI flag**: Launch the GUI directly from the CLI without going through
  `python -m apple_device_cli.gui_qt`.
- **`ios-enroll-gui` console script**: Separate entry point registered in `pyproject.toml`,
  mirrors `ios-enroll` but launches the GUI.
- **Nuitka per-target build extras**: `[build]` extra added (`nuitka`, `ordered-set`, `zstandard`)
  with per-target extras gating (`gui` builds also include `PySide6`). Cross-platform build
  pipeline produces native onefile binaries for both `ios-enroll` and `ios-enroll-gui`.
- **GitHub Actions CI**: `.github/workflows/build.yml` builds Linux + Windows artifacts on
  every push; Windows job builds `ios-enroll.exe` and `ios-enroll-gui.exe` via MSVC.
- **Windows install guide**: `docs/INSTALL_WINDOWS.md` covering Python install, iTunes /
  Apple Mobile Device Service, libusb, and the GUI launch path.

### Bug Fixes
- **Re-enrollment exit code**: `enroll re-enroll` now prints error message and exits with code 1 on failure
- **Keybag cleanup**: Sensitive keybag files are now deleted after enrollment completes (ensured via `finally` block)
- **Profile list iteration**: Robust handling of both dict and list formats from pymobiledevice3
- **Re-enrollment polling**: Device reconnection now polled instead of fixed 30-second sleep
- **State rename**: `is_mdm_managed` renamed to `was_mandatorily_unpaired` for accuracy
- **Dead code removal**: Unused `enrollment/flows.py` and associated test files deleted
- **Build script defaults**: `build_windows.bat` now defaults to `windows` target (was `all` -> Linux ELF), and `python build_nuitka.py all` now means "all builds for the current platform" (was invoking MinGW cross-compile on Linux CI). Affects CI job correctness.
- **Install guide paths**: `docs/INSTALL_WINDOWS.md` artifact paths corrected to match actual Nuitka `--output-filename` behavior (`dist\ios-enroll.exe`, not `dist\ios-enroll\ios-enroll.exe`). AV section corrected (Nuitka, not PyInstaller).
- **Friendly PySide6 hint**: `ios-enroll --gui` / `ios-enroll-gui` on a system without PySide6 now shows a single-line install hint instead of a top-level `ImportError` traceback.
- **Bare-mock spec enforcement**: AGENTS.md documents the bare-`MagicMock()` antipattern; all class-shaped mocks in the test suite now use `spec=RealClass`.

### Changed
- **fcntl locking**: `save_org()` and `import_mobileconfig()` now use cross-process `fcntl.flock` to prevent races
- **Keybag cleanup**: Extracted `_cleanup_keybag()` helper, wrapped body in `try/finally` to guarantee cleanup
- **TemporaryDirectory**: Removed empty wrapper around supervised pairing body
- **Cross-platform org lock**: `OrganizationManager` now uses `fcntl`/`msvcrt` cross-platform wrappers for org-file locking (no behavior change on Linux/macOS, enables concurrent-safety on Windows).
- **Pytest conftest hard-fail**: `tests/conftest.py` now raises `ImportError` at load time if `pymobiledevice3` isn't installed (was: silent skip).

### Docs
- **Activation state string**: Documented that `"Unactivated"` is a shared pymobiledevice3 convention
- **Skip pane mapping**: Documented `apple-pay` → `Payment` mapping per Apple's `skipkeys.yaml`
- **Architecture**: Noted `flows.py` deletion in module docs
- **AGENTS.md mock-spec rule**: New anti-pattern table + dedicated "Mock Spec'ing" section explaining why bare `MagicMock()` is forbidden and how to use `spec=` correctly.

### Testing
- **Coverage**: Added tests for lock acquisition on import, keybag helper in isolation, concurrent save+import contention, cert-load exception cleanup, and re-enroll success path
- **Test reliability**: Fixed timing-window flakiness in concurrent lock test using `threading.Event`
- **Bare-mock conversion**: All class-shaped mocks in the existing test suite converted from bare `MagicMock()` to `spec=RealClass`.

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

