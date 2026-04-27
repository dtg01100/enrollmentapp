# Changelog

## v0.1.0 (2026-04-27)

Initial release: iOS supervised enrollment CLI for Linux.

### Features
- **CLI**: Typer-based `ios-enroll` command for device management
- **Organizations**: Create, delete, show, import, export with PKCS12 identity support
- **Device operations**: list, info, erase, restore, update
- **Enrollment**: supervised pairing, activation, guided-enroll
- **Skip panes**: Presets for Setup Assistant configuration
- **Import**: Apple Configurator .organization and MDM .mobileconfig files
- **Identity**: Self-signed CA and server certificate generation
- **Linux support**: USB/udev rules for Apple devices (normal, recovery, DFU modes)

### Technical
- Built on pymobiledevice3 and libimobiledevice
- Organization storage in `~/.config/apple_device_cli/orgs/`
- Comprehensive test suite

