# Enrollment Flow Architecture

## Overview

Enrollment prepares an iOS device for supervised MDM management. The core
implementation is `do_supervised_pairing()` in `enrollment/supervised.py`,
called by `make_supervised()` (the public sync wrapper) and driven by the
CLI commands `enroll make-supervised` and `enroll guided-enroll`.

## EnrollmentResult

`do_supervised_pairing()` returns an `EnrollmentResult` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Operation completed without error |
| `device_udid` | `str \| None` | Device UDID |
| `supervised` | `bool` | Device supervision state |
| `mdm_enrolled` | `bool` | MDM enrollment state |
| `wifi_installed` | `bool` | WiFi profile was installed |
| `errors` | `list[str]` | Error messages encountered |
| `cloud_config` | `dict \| None` | Cloud configuration dict (if readable) |

## Flow Steps

`do_supervised_pairing` executes these steps in order:

1. **Connect** — Lockdown connection via pymobiledevice3 `create_using_usbmux()`
2. **Activation check** — If state is `"Unactivated"`, run `activation_svc.activate()`
3. **Supervise** — `svc.set_cloud_configuration()` with org identity, skip panes, MDM URL
4. **Reconnect** — If device disconnected (BrokenPipeError/OSError), wait up to 30s with `_wait_for_device_reconnect()`
5. **WiFi install** — Optional WiFi profile via `svc.install_wifi_profile()` or custom mobileconfig
6. **MDM profile install** — Either `svc.install_profile_silent(keybag, payload)` (escalated) or `svc.store_profile(payload, PostSetupInstallation)` (deferred)
7. **Verify** — Read back cloud config, confirm `IsSupervised`

## Keybag Lifecycle

A keybag is a temporary PEM file containing the supervision cert + key used
for escalating MDM profile installation:

- **Created** at `/tmp/ios_enroll_keybag_<random>` before the supervised try block
- **Used** in `install_profile_silent(keybag_path, payload_bytes)` for privileged MDM install
- **Cleaned up** in `finally` block via `_cleanup_keybag(keybag_path)` — always runs, swallows OSError

Functions: `_create_keybag_file_from_identity()` (preferred), `create_keybag_file()` (fallback),
`_load_cert_public_bytes_from_keybag()` (extracts cert for cloud config payload).

## Cloud Configuration Error Handling

When `set_cloud_configuration()` raises `CloudConfigurationAlreadyPresentError`:

1. The existing cloud config is read back via `svc.get_cloud_configuration()`
2. Compared with desired config using `_cloud_config_matches()`
3. If matching → treated as success, enrollment proceeds
4. If not matching → error reported, config_set stays False

This makes supervision **idempotent** — re-running on an already-supervised
device with the same org config is safe.

## MDM Profile Install Strategy

```
if keybag_path exists:
    svc.install_profile_silent(keybag_path, payload_bytes)    # preferred
else:
    svc.store_profile(payload_bytes, Purpose.PostSetupInstallation)  # deferred
```

**Preferred path** (`install_profile_silent`): Escalates via keybag to install
MDM profile immediately. Requires keybag (cert+key on disk) and that the device
can reach the MDM server (WiFi must be working).

**Fallback path** (`store_profile`): Stores profile for Setup Assistant to install
after device reboot. Works without keybag but requires device to go through setup.

**Retry**: 3 attempts with 5-second backoff for transient network errors
(checked via `_is_transient_mobileconfig_network_error`). Controlled by
`fail_on_mdm_error` parameter.

## Key Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `do_supervised_pairing()` | `supervised.py:449` | Core async supervision implementation |
| `make_supervised()` | `supervised.py:827` | Sync wrapper for `do_supervised_pairing` |
| `erase_device_for_reenrollment()` | `supervised.py:946` | Clear cloud config from device |
| `get_device_enrollment_state()` | `supervised.py:1036` | Read back current enrollment state |
| `validate_enrollment_prerequisites()` | `supervised.py:965` | Check cert/key/org/MDM before enrolling |
| `interactive_enroll()` | `cli.py:197` | Guided CLI workflow (step-by-step) |

## Testing

```bash
# All enrollment tests
PYTHONPATH=src python -m pytest tests/test_enrollment.py tests/test_enrollment_flow_fixes.py -v

# Specific regression test
PYTHONPATH=src python -m pytest tests/test_enrollment_flow_fixes.py::TestCloudConfigBugFix -v
```

### Test categories

- **Supervised pairing tests** (`test_enrollment.py`): `make_supervised()` with
  invalid paths, WiFi profiles, MDM install, error formatting, activation
- **Regression tests** (`test_enrollment_flow_fixes.py`): Cloud config, state
  validation, keybag persistence, WiFi/MDM ordering, keybag cleanup, exit codes

## Troubleshooting

### MDM silent install fails with "network error"

Transient timing issue: the device's WiFi may not be fully established yet
when MDM install is attempted. The flow retries 3 times with 5-second
backoff. If persistent:

1. Verify the device has a working WiFi connection
2. Run MDM profile install manually after Setup Assistant completes
3. Check MDM server URL is reachable from the device's network
4. Consider using `--fail-on-mdm-error` to control whether this is fatal

### Cloud config already present

If cloud config exists and **matches** the desired org, the flow proceeds
normally. If it **does not match**, the flow reports an error. To change
orgs, use `enroll re-enroll` to clear the cloud config first.

### Device shows "Supervised: True" but MDM not enrolled

Deferred enrollment pattern — the MDM profile was stored for Setup Assistant
to install during device setup/reboot. This happens when:
- No keybag file was available (falls back to `store_profile`)
- `install_profile_silent` failed and the profile was stored instead

### Device State Unclear

```python
from apple_device_cli.enrollment.supervised import get_device_enrollment_state

state = get_device_enrollment_state(udid)
# Returns dict with: activation_state, is_supervised, is_mdm_removable,
# cloud_config_applied, supervision_identity, errors
```
