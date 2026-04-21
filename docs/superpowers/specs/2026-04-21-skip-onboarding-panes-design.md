# Skip Onboarding Panes — Design Spec

**Date:** 2026-04-21  
**Feature:** Named preset system for skipping iOS Setup Assistant panes during supervised enrollment

---

## Overview

When an iOS device is supervised via `make-supervised`, the operator can optionally suppress specific Setup Assistant (onboarding) panes so users never see them. This is implemented by sending a `SetCloudConfiguration` request to the device via `MobileConfigService` after pairing, with a `SkipSetup` list embedded in the cloud configuration payload.

---

## Mechanism

`pymobiledevice3.services.mobile_config.MobileConfigService.set_cloud_configuration()` sends a plist dict to the device containing:

```python
{
    "AllowPairing": True,
    "CloudConfigurationUIComplete": True,
    "ConfigurationSource": 2,
    "ConfigurationWasApplied": True,
    "IsMDMUnremovable": False,
    "IsMandatory": True,
    "IsMultiUser": False,
    "IsSupervised": True,
    "OrganizationMagic": "<uuid>",
    "OrganizationName": "<org name>",
    "PostSetupProfileWasInstalled": True,
    "SkipSetup": ["Location", "Siri", ...],
    "SupervisorHostCertificates": [<DER public key bytes>],
}
```

This is applied **after** supervised pairing, while the device is booted to iOS (not during restore).

---

## Presets

Three named presets expand to sets of `SkipSetup` pane identifiers:

### `minimal`
Skips locale/connectivity bootstrapping only — appropriate when you still want users to complete their own account setup:
- `Language`, `Region`, `SIMSetup`, `WiFi`

### `standard`
Skips common business enrollment panes — users see no Apple ID prompts, no privacy nags, no passcode setup:
- Everything in `minimal` plus: `AppleID`, `Siri`, `ScreenTime`, `Diagnostics`, `Passcode`, `Biometric`, `Privacy`, `Appearance`, `Welcome`, `Restore`, `RestoreCompleted`, `UpdateCompleted`, `SoftwareUpdate`, `Android`, `TOS`, `IntendedUser`

### `all`
Skips every pane pymobiledevice3 knows about (sourced from `mobile_config.py` `supervise()` method). Produces the same result as Apple Configurator's "Skip all steps" checkbox.

---

## CLI Interface

`--skip-preset` and `--skip` are both optional and additive. Their resolved lists are merged (deduplicated) before being sent to the device.

```bash
# Preset only
./enroll.py make-supervised --udid <UDID> --org-name "My Org" --skip-preset standard

# Preset plus extra panes
./enroll.py make-supervised --udid <UDID> --org-name "My Org" --skip-preset standard --skip Biometric AppStore

# Individual panes only (no preset)
./enroll.py make-supervised --udid <UDID> --org-name "My Org" --skip AppleID Siri

# No skips (default — same behavior as before)
./enroll.py make-supervised --udid <UDID> --org-name "My Org"
```

`--skip` accepts one or more space-separated pane names. Names are case-insensitive on input and normalized to their canonical forms before being sent to the device.

The same flags apply to the `enroll` (full workflow) command.

---

## Implementation

### New module: `skip_panes.py`

Extracted into its own module for clarity and testability:

```python
PRESETS = {
    "minimal": ["Language", "Region", "SIMSetup", "WiFi"],
    "standard": [...],  # minimal + business panes
    "all": [...],       # full list from mobile_config.py
}

# Canonical name map for case-insensitive input normalization
PANE_NAME_MAP = {name.lower(): name for name in ALL_PANE_NAMES}

def resolve_skip_panes(preset: str | None, extra: list[str]) -> list[str]:
    """Merge preset + extra pane names into a deduplicated canonical list."""
    ...
```

### Changes to `IOSEnrollmentTool.make_supervised()`

Currently a stub. Will be implemented to:

1. Load org supervision identity (cert + key) from org manager or explicit `-C`/`-K` flags
2. Call `lockdown.pair_supervised(keybag_pem_path)` via pymobiledevice3 Python API (creates `/var/lib/lockdown/<udid>.plist`)
3. Re-open lockdown connection (now trusted), then open `MobileConfigService` and call `set_cloud_configuration()` with:
   - `IsSupervised: True`
   - `OrganizationName` from org
   - `SkipSetup` from `resolve_skip_panes(preset, extra)`
   - `SupervisorHostCertificates`: org cert public key as DER bytes
4. Print confirmation with the preset/pane list applied

### Changes to `enroll.py` argument parser

- Add `--skip-preset {minimal,standard,all}` to `make-supervised` and `enroll` subparsers
- Add `--skip` (nargs=`+`) to `make-supervised` and `enroll` subparsers
- Remove existing partial `--skip-*` flags from `prepare` that overlap (or keep for backward compat)

---

## Error Handling

- Unknown pane name in `--skip`: warn and skip (don't abort), since Apple adds new panes over time
- `CloudConfigurationAlreadyPresentError`: surface a clear message — device already has a cloud config applied; user must erase and re-enroll
- Device not yet paired: fail fast with a clear error before attempting `set_cloud_configuration`

---

## Out of Scope

- Storing a default skip preset in `org.json` (deferred — can be added later as Option C)
- Baking skip panes into the restore ramdisk (not needed; post-boot SetCloudConfiguration works)
- GUI (Gradio) skip pane picker (deferred)
