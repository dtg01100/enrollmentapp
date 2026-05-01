# Enrollment Flow Architecture & Improvements

## Overview

This document describes the iOS device enrollment flow architecture, recent improvements, and design patterns.

## What Are Enrollment Flows?

An **enrollment flow** is a sequence of operations that prepares an iOS device for supervised management. The main flows are:

1. **Simple Supervised** — Apply supervision to a clean device
2. **Reenrollment** — Clear cloud config and re-enroll with new organization/MDM
3. **Guided (Interactive)** — Interactive workflow combining device selection, org setup, and supervision

## Architecture Improvements

### 1. Fixed Cloud Configuration Bug

**Problem**: Cloud configuration was only set when `skip_list` was provided. This caused `IsSupervised` state to be incorrectly reported as `False` when no panes were being skipped.

**Fix**: Cloud configuration is now **always set** after supervision, regardless of skip_list. Skip panes are conditionally included only if `skip_list` is provided.

**Impact**: Device supervision state is now always correctly reported.

```python
# Before: Only set if skip_list provided
if skip_list:
    await svc.set_cloud_configuration({...})

# After: Always set, skip_list is optional within
cloud_config = {...}  # Base config
if skip_list:
    cloud_config["SkipSetup"] = _map_skip_setup(skip_list)
await svc.set_cloud_configuration(cloud_config)
```

### 2. Simplified Device State Machine

**Problem**: Erase decision logic had multiple overlapping conditions and a fallthrough `else` clause that always erased. This made state transitions unclear and hard to verify.

**Fix**: Replaced with explicit state machine:

```
Device State → Erase Needed?
───────────────────────────
Has cloud config → YES (any state)
Supervised without cloud config → YES (error state)
Activated & clean → NO
Unactivated & clean → NO
```

**Benefits**:
- No fallthrough cases
- Impossible states caught explicitly
- Clear reasoning about each decision
- Simpler to test and maintain

### 3. Created Reusable Flows Module

**Problem**: All business logic was embedded in CLI commands, making it impossible to reuse for other UIs (API, TUI, etc.).

**Fix**: Created `enrollment/flows.py` with high-level flow classes:

```python
from apple_device_cli.enrollment.flows import (
    SimpleSupervisedEnrollment,
    ReenrollmentFlow,
    FlowRegistry,
)

# Use from any UI
flow = FlowRegistry.get("simple-supervised")
result = flow.execute(org=org, udid=udid, skip_list=[...])
```

**Benefits**:
- Business logic decoupled from CLI
- Flows can be composed and extended
- Easy to test without TUI mocking
- Enables API and other UIs

### 4. Standardized Error Handling

**Problem**: Different flows used different error handling patterns (raise, return result, swallow), making behavior unpredictable.

**Fix**: All flows now return `EnrollmentResult` with:
- `success: bool` — Operation succeeded
- `device_udid: str` — Device that was enrolled
- `supervised: bool` — Device supervision state
- `mdm_enrolled: bool` — MDM enrollment state
- `errors: list[str]` — Any errors encountered
- `cloud_config: dict` — Final cloud config state

## Flow Design Patterns

### SimpleSupervisedEnrollment

Direct supervision without erase. Use for:
- Fresh devices
- Devices already in correct activation state
- When erase is handled separately

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
    print(f"✓ Device {result.device_udid} supervised")
else:
    print(f"✗ Errors: {result.errors}")
```

### ReenrollmentFlow

Clear cloud config and re-enroll. Use for:
- Devices already enrolled but need new org
- Changing MDM servers
- Re-enrollment after policy changes

```python
from apple_device_cli.enrollment.flows import ReenrollmentFlow

flow = ReenrollmentFlow()
erase_ok, result = flow.execute(org=new_org, udid=udid)

if not erase_ok:
    print("✗ Failed to clear cloud config")
elif result.success:
    print("✓ Device re-enrolled with new org")
else:
    print(f"✗ Enrollment errors: {result.errors}")
```

### GuidedEnrollmentWorkflow (CLI-specific)

Still exists in `cli.py` but now uses flows internally and provides interactive prompts.

## Device State Machine

```
┌─────────────────────────────────────────────────────────┐
│                  DEVICE STATE MACHINE                    │
└─────────────────────────────────────────────────────────┘

Fresh Device (Unactivated, not supervised, no cloud config)
    ↓ [No erase needed]
    ├─→ Activate (if needed)
    └─→ SimpleSupervisedEnrollment → Supervised

Activated Clean (Activated, not supervised, no cloud config)
    ↓ [No erase needed]
    └─→ SimpleSupervisedEnrollment → Supervised

Already Enrolled (Activated, supervised, cloud config)
    ↓ [ERASE REQUIRED]
    └─→ ReenrollmentFlow (erase) → Fresh → Supervised with new org

Error State (Supervised without cloud config)
    ↓ [ERASE REQUIRED - shouldn't happen]
    └─→ Full erase/restore
```

## Testing

### Test Coverage

- **Cloud Config Bug Fix**: Tests verify cloud config always set
- **State Validation**: Tests verify invalid states caught
- **Error Handling**: Tests verify errors properly reported
- **Flow Registry**: Tests verify flows discoverable and extensible
- **Integration**: Tests verify flows produce correct EnrollmentResult

### Running Tests

```bash
# All enrollment tests
pytest tests/test_enrollment*.py -v

# New flow tests
pytest tests/test_enrollment_flows.py -v

# Specific test class
pytest tests/test_enrollment_flow_fixes.py::TestCloudConfigBugFix -v
```

## Future Work

### Short Term
- Add device state verification before operations (safety check)
- Add logging to flows for better debugging
- Improve progress callback granularity

### Medium Term
- Create API endpoint wrapper for flows
- Add flow cancellation/rollback support
- Support parallel enrollment of multiple devices

### Long Term
- Async/await unification across codebase
- Event-driven flow orchestration
- Advanced state machine with explicit transitions
- Enrollment flow analytics and metrics

## Code References

### Main Files

- `src/apple_device_cli/enrollment/flows.py` — Enrollment flows
- `src/apple_device_cli/enrollment/supervised.py` — Supervision implementation
- `src/apple_device_cli/cli.py` — CLI commands (uses flows)
- `tests/test_enrollment_flow*.py` — Flow tests

### Key Classes

```python
EnrollmentResult         # Standard result with errors, state
EnrollmentFlow          # Base class for flows
SimpleSupervisedEnrollment  # Direct supervision flow
ReenrollmentFlow        # Clear config and re-enroll
FlowRegistry            # Extensible flow registry
```

### Key Functions

```python
make_supervised()               # Low-level supervision
erase_device_for_reenrollment() # Cloud config erase
validate_enrollment_prerequisites()  # Pre-flight checks
```

## Migration Guide

### For CLI Commands

Old pattern (direct calls):
```python
from apple_device_cli.enrollment.supervised import make_supervised

result = make_supervised(cert_path, key_path, org_name, skip_list=...)
```

New pattern (using flows):
```python
from apple_device_cli.enrollment.flows import SimpleSupervisedEnrollment

flow = SimpleSupervisedEnrollment()
result = flow.execute(org=org, udid=udid, skip_list=...)
```

### For Testing

Old pattern (mocking CLI):
```python
# Complex mocking of CLI prompts and device state
```

New pattern (mocking flows):
```python
flow = SimpleSupervisedEnrollment()
# Pass mocked org, device info; test result
result = flow.execute(org=mock_org, udid="test-udid")
assert result.supervised
```

## Troubleshooting

### Cloud Config Not Applied

Check that:
1. No exceptions in set_cloud_configuration() call
2. Device is connected and responding
3. Device can reach lockdown service

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
