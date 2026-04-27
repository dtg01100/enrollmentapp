# Profile Install — Design Spec

**Date:** 2026-04-21
**Feature:** Install .mobileconfig profile files onto supervised iOS devices via the enrollment CLI

---

## Overview

The CLI gains a `profile install` command that reads an existing `.mobileconfig` file and installs it on a supervised iOS device. Profile installation is needed to push MDM payloads (WiFi configs, certificates, app configuration) onto devices as part of enrollment workflows.

---

## Mechanism

`pymobiledevice3.services.mobile_config.MobileConfigService` exposes two installation methods:

- `install_profile(payload_bytes)` — standard interactive install. The device shows a UI prompt asking the user to accept the profile. Works on unsupervised devices.
- `install_profile_silent(keybag_file, profile_bytes)` — silent install with escalation. Uses the org keybag to sign an escalation challenge, allowing MDM-signed profiles to install without user interaction. **Requires the device to be supervised.**

For supervised devices, `install_profile_silent` is the correct approach — it matches how Apple Configurator installs profiles silently during enrollment.

---

## CLI Interface

```bash
./enroll.py profile install --path <file.mobileconfig> --udid <UDID> --org-name "My Org"
```

**Arguments:**
- `--path` (required): path to `.mobileconfig` file
- `--udid` (required): device UDID
- `--org-name` (optional): organization name; uses org manager if not provided
- `-C` / `--cert`: explicit path to org certificate DER (overrides org manager)
- `-K` / `--key`: explicit path to org private key DER (overrides org manager)

**Behavior:**
- If device is supervised → use `install_profile_silent(keybag, profile_bytes)`
- If device is NOT supervised → fall back to `install_profile(profile_bytes)` with a warning
- Profile bytes read directly from file; no parsing required (pymobiledevice3 handles the plist decode internally)

---

## Implementation

### New method: `IOSEnrollmentTool.install_profile()`

```
def install_profile(self, path, udid=None, org_name=None, cert_path=None, key_path=None):
    """Install a .mobileconfig profile on the device."""
```

Steps:
1. Read profile bytes from `path`
2. Resolve org identity (explicit `-C`/`-K`, or org manager lookup by `org_name`)
3. Connect to device via lockdown
4. Check `IsSupervised` via lockdown value
5. If supervised: build keybag PEM, call `install_profile_silent(keybag_pem, profile_bytes)`
6. If unsupervised: call `install_profile(profile_bytes)` with warning
7. Print confirmation with profile name and install result

### CLI argument parser

Add `profile` subcommand with `install` subcommand:
```python
profile_parser = subparsers.add_parser("profile", help="Profile management")
profile_sub = profile_parser.add_subparsers(dest="profile_action")

install_parser = profile_sub.add_parser("install", help="Install a .mobileconfig profile")
install_parser.add_argument("--path", required=True, help="Path to .mobileconfig file")
install_parser.add_argument("--udid", required=True, help="Device UDID")
install_parser.add_argument("--org-name", help="Organization name")
install_parser.add_argument("-C", "--cert", help="Path to certificate DER")
install_parser.add_argument("-K", "--key", help="Path to private key DER")
```

### Error handling

- File not found / not readable: `argparse` handles via `required=True`
- Device not paired: pymobiledevice3 raises `NotPairedError` — surface with "Device must be paired first. Run `./enroll.py prepare` first."
- Profile install fails: pymobiledevice3 exception propagated with original message
- Org not found: error "Organization not found — use `--org-name` or `-C`/`-K` to specify"

---

## Out of Scope

- Profile removal (future feature)
- Profile listing (future feature)
- Parsing/modifying profile contents before install
- GUI profile install tab
- Installing on devices in recovery mode (must be booted to iOS)

---

## Test approach

- Unit test `resolve_skip_panes` (existing)
- Unit test profile install with mocked lockdown/service
- Integration test on real device (iPad)