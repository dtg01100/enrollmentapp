# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Testing
- **Restore tests are hermetic again.** `test_restore_engine.py` no longer
  requires `irecovery` / `ipsw` / `idevicerestore` on the host PATH — the
  subprocess-layer tests stub `shutil.which` so they run on any machine
  (23 tests previously failed with "`irecovery` is required to exit
  recovery mode" etc. on hosts without those tools).
  `test_restore_tab.py`'s cache-picker test now uses a `tmp_path` folder
  instead of the hardcoded `/var/mnt/Disk2/iosfirmwares`, which is
  unwritable on many hosts.

### Fixed
- **CI: Windows Nuitka build green again.** `build_nuitka.py windows*`
  targets now pick MSVC (`--msvc=latest`) on native Windows hosts instead
  of unconditionally passing `--mingw64`, which Nuitka rejects on Python
  3.13+ — the Build Executables workflow's Windows job was dying
  immediately with `cannot use '--mingw64' on Python version 3.13 or
  higher`. MinGW cross-compiles from Linux/macOS still work on Python
  ≤ 3.12 hosts, and newer hosts get a clear hint instead of a Nuitka
  FATAL.
- **CI: release creation no longer 403s.** `build.yml` now declares
  `permissions: contents: write`, so the workflow token can create GitHub
  releases on tag pushes. The repo's default workflow permission is
  read-only, which made `softprops/action-gh-release` fail with
  `GitHub release failed with status: 403` → `Too many retries`.

## [1.3.1] - 2026-08-07

### Added
- **On-demand IPSW hash verification.** New `ios-enroll device
  verify-ipsw --ipsw PATH [--device PRODUCT_TYPE]` CLI subcommand
  and a "Verify (ipsw.me)" button on the Restore tab. Stream-hashes
  the local file (SHA-1 + SHA-256) and compares against ipsw.me's
  published hashes (`api.ipsw.me/v4/device/<device>?type=ipsw`).
  Reports MATCH/MISMATCH per field. The CLI exits 0 on full match,
  1 on any mismatch, 0 with a warning when ipsw.me can't be reached.
- **Cached-firmware markers.** The Restore tab's signed-versions list
  appends `(cached)` to entries whose IPSW is already in the cache
  directory, so the user sees at a glance whether Start will download
  or use a local file.
- Restore tab "Restore log" → "Activity log" relabel with a styled
  header to match the new layout.

### Fixed
- **`idevicerestore -i` ECID format.** The engine now passes ECID
  with the `0x` prefix (`0x00094daa01d80032`); the bare-hex form
  was rejected with "Could not parse ECID". `_device_ecid` and
  `recovery_device_descriptor` return the `0x`-prefixed form.
- **GUI restore on Recovery-mode devices.** Selecting the synthetic
  "(Recovery mode)" combo entry now enables Start, populates the
  versions combo from locally-cached IPSW files (since a Recovery-
  mode device can't fetch signed versions over lockdown), and routes
  the restore by ECID — `_start_restore` no longer passes the SRNM
  as a bogus UDID.
- **Progress bar stuck at 0% during uploads.** The parser now
  recognizes the actual `idevicerestore -P` output format
  (`Uploading:   0.5` — colon + decimal fraction), not just the
  `Uploading [====...] 49.7%` bracket-bar format.
- **Test suite hermetic against live recovery devices.** The
  `make_app` test fixture now defaults `detect_recovery_devices_present`
  to False, so a real Recovery-mode iPad on the USB bus doesn't leak
  a synthetic combo entry into tests.

### Changed
- **Restore tab layout restructured.** Device / Firmware sections are
  now in `QGroupBox`es, the iOS Version + Refresh are on one row,
  Browse and Verify are paired with the IPSW file label, Start Restore
  is the visually prominent primary action (bold + taller), and the
  Progress bar is anchored to the bottom of the tab inside a vertical
  `QSplitter` so it never gets pushed off-screen.
- **Enrollment tab restructured.** Two `QGroupBox`es (Organization &
  device / WiFi optional), "Use Selected Device" paired with the UDID
  combo, and Make Supervised is the prominent primary action.
- **Devices and Organizations tabs** show helpful empty-state
  placeholders ("No devices found. Connect an iOS device...", "No
  organizations yet. Click Create Org or Refresh Orgs.") instead of
  blank space.
- **Shared log widget** is now capped at max 180 px / min 80 px so a
  verbose operation can't squeeze the tab content to zero height.

## [1.3.0] - 2026-08-07

### Added
- Restore tab in `ios-enroll-gui` with device picker, signed-version
  dropdown (`ipsw --urls`), Browse for local IPSW, mode label
  (Normal / Recovery / Restore / DFU), Enter Recovery and Exit
  Recovery buttons, Refresh Devices, and a real `QProgressBar` driven
  by `idevicerestore -P` events.
- Cache hit short-circuit for IPSW downloads (no re-download if
  the final file is already present and non-empty) and streamed
  download progress events.

### Fixed
- **Event-loop bug in `enter_recovery_mode`.** Two `asyncio.run()`
  calls left a `Future` attached to a different loop; consolidated
  to a single event loop.
- **Recovery-mode restore targeting.** `idevicerestore -u <udid>`
  only works in Normal mode; the engine now detects mode and passes
  `-i <ecid>` for Recovery/restore/DFU-mode devices. CLI gained
  `--ecid` option; GUI falls back to ECID when the combo is empty.
- **Exit Recovery from any state.** The combo-driven Exit Recovery
  used to silently no-op when the combo was empty (because recovery
  devices are invisible to usbmuxd). Added "Exit Recovery (any)"
  fallback that scans the USB bus unconditionally; recovery devices
  in the bus also get a synthetic "(Recovery mode)" combo entry.
- **`exit_recovery_mode` now runs `irecovery --normal`.** The
  previous `device.reset()` only rebooted iBSS, which re-entered
  the recovery loop.

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
- Restore tab refresh button (was already in spec but wiring verified) and a device mode label (Normal / Recovery / Restore / DFU) that updates when the device list refreshes. "Enter Recovery" and "Exit Recovery" buttons on the Restore tab. The Exit Recovery button works even when no device is selected in the dropdown (since a device in Recovery mode doesn't show up in the lockdown list).

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

