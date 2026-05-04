# Enrollment Flow Architecture

## Overview

This document describes the enrollment flow architecture in `ios-enroll`.

## What Are Enrollment Flows?

An **enrollment flow** is a sequence of operations that prepares an iOS device for supervised management. Flows are implemented as standalone, reusable classes in `enrollment/flows.py`.

The main flows are:

1. **Simple Supervised** — Apply supervision to a clean device (no erase needed)
2. **Reenrollment** — Clear cloud config and re-enroll with a new organization or MDM server
3. **Guided (Interactive)** — CLI workflow combining device selection, org setup, skip panes, and supervision

## Architecture

### Design Principles

- **Reusable**: Flows live in `enrollment/flows.py` and can be called from any UI (CLI, API, TUI).
- **Idempotent where possible**: Operations like cloud config application detect existing matching configuration and treat it as success.
- **Progressive**: Complex operations (guided enrollment) are composed from simpler reusable flows.
- **Observable**: All flows report progress via an optional callback and return a structured `EnrollmentResult`.

### EnrollmentResult

All flows return an `EnrollmentResult`:

```python
@dataclass
class EnrollmentResult:
    success: bool              # Operation completed without error
    device_udid: str            # Device UDID
    supervised: bool            # Device supervision state
    mdm_enrolled: bool          # MDM enrollment state
    cloud_config_applied: bool  # Cloud config was applied
    errors: list[str]           # Error messages encountered
    warnings: list[str]         # Non-fatal warnings
```

## Flow Reference

### SimpleSupervisedEnrollment

Direct device supervision without erase. Use for clean devices where no existing enrollment needs to be cleared.

```python
from apple_device_cli.enrollment.flows import SimpleSupervisedEnrollment

flow = SimpleSupervisedEnrollment()
result = flow.execute(
    org=org,
    udid="device-udid",
    skip_list=["location", "passcode"],
    progress_callback=lambda msg: print(f"  {msg}")
)

if result.success:
    print(f"Device {result.device_udid} supervised")
else:
    print(f"Errors: {result.errors}")
```

**When to use:**
- Fresh devices with no prior enrollment
- Devices already activated but not yet supervised
- When erase is handled separately by the caller

**When not to use:**
- Device already has cloud config from a prior enrollment → use `ReenrollmentFlow`
- Device is in an error state (supervised but no cloud config) → erase first

### ReenrollmentFlow

Clear existing cloud config and re-enroll with a new or updated organization/MDM configuration.

```python
from apple_device_cli.enrollment.flows import ReenrollmentFlow

flow = ReenrollmentFlow()
erase_ok, result = flow.execute(org=new_org, udid=udid)

if not erase_ok:
    print("Failed to clear cloud config")
elif result.success:
    print("Device re-enrolled with new org")
else:
    print(f"Enrollment errors: {result.errors}")
```

**When to use:**
- Device already enrolled under a different organization
- Changing MDM servers
- Re-enrollment after policy changes

**When not to use:**
- Fresh device with no cloud config → use `SimpleSupervisedEnrollment`

### GuidedEnrollmentWorkflow

The interactive CLI workflow (`ios-enroll enroll guided-enroll`). Combines device selection, MDM server configuration, organization setup, skip pane selection, erase decision, and supervision in a single command.

Not a reusable class — this is the CLI's primary user-facing enrollment interface.

## Device State Machine

```
[Unactivated / Clean / No cloud config]
    │
    ├─── No erase needed
    │       │
    │       └── SimpleSupervisedEnrollment ──→ [Supervised / MDM enrolled]

[Activated / Supervised / Cloud config present]
    │
    ├─── Erase required
    │       │
    │       ├── ReenrollmentFlow (erase + clear config)
    │       │           │
    │       │           └── Fresh device state → SimpleSupervisedEnrollment
    │       │
    │       └── Device re-enrolled with new org

[Supervised / No cloud config — error state]
    │
    └─── Erase + full restore required
```

## Key Functions

### Low-level (in `supervised.py`)

| Function | Purpose |
|----------|---------|
| `make_supervised()` | Core async supervision implementation |
| `erase_device_for_reenrollment()` | Clear cloud config from device |
| `get_device_enrollment_state()` | Read back current enrollment state |

### Flow orchestration (in `flows.py`)

| Class | Purpose |
|-------|---------|
| `SimpleSupervisedEnrollment` | Direct supervision without erase |
| `ReenrollmentFlow` | Clear config and re-enroll |

## Testing

```bash
# All enrollment tests
python -m pytest tests/test_enrollment*.py -v

# Flow-only tests
python -m pytest tests/test_enrollment_flow*.py -v

# Specific regression test
python -m pytest tests/test_enrollment_flow_fixes.py::TestCloudConfigBugFix -v
```

### Test categories

- **Unit tests** (`test_enrollment.py`): Individual functions, error formatting, path normalization
- **Flow tests** (`test_enrollment_flows.py`): Flow classes and state machine logic
- **Regression tests** (`test_enrollment_flow_fixes.py`): Bug-specific fixes (cloud config reuse, MDM retry, status readback)
- **Integration tests** (`test_enrollment_integration.py`): Full end-to-end flows with mocked device layer
- **Redaction tests** (`test_redaction.py`): Privacy sanitization output

## Troubleshooting

### MDM silent install fails with "network error"

This is usually a transient timing issue: the device's WiFi may not be fully established yet when MDM profile install is attempted. The `make_supervised()` flow retries automatically up to 3 times with a 5-second backoff. If it continues to fail, try:
1. Verify the device has a working WiFi connection
2. Run MDM profile install manually after Setup Assistant completes
3. Check MDM server URL is reachable from the device's network

### "Failed to re-configure" error

When cloud config already exists and matches the requested configuration, this is now treated as success. If you see this error with no actionable detail, check:
1. Device lockdown service is responding
2. The supervision identity (cert/key) hasn't expired
3. MDM URLs in the org match what the MDM server expects

### Device shows "Supervised: True" but MDM not enrolled

This is the normal deferred enrollment pattern. MDM profile installation is deferred to Setup Assistant when the device is powered. The MDM profile is stored in the org directory and installed during the guided Setup Assistant flow.

### Device State Unclear

Check device state:
```python
from apple_device_cli.enrollment.supervised import get_device_enrollment_state

state = get_device_enrollment_state(udid)
# Returns dict with: activation_state, is_supervised, cloud_config_applied, etc.
```

### Reenrollment Fails

Common issues:
1. Device not in normal mode (needs recovery mode exit)
2. Cloud config erase timeout (wait 60s)
3. Device lockdown connection lost (reconnect device)

## Summary

Recent improvements have made enrollment flows:
- **More Reliable**: Cloud config always set, state machine explicit
- **More Reusable**: Extracted into flows module
- **More Testable**: Clear interfaces, no CLI coupling
- **More Maintainable**: Reduced complexity, clear patterns

The architecture now supports extension to new UIs and flow types while maintaining backward compatibility with existing CLI commands.
