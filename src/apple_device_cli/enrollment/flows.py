"""High-level enrollment flow orchestration.

This module provides reusable enrollment flows that can be called from CLI or other UIs.
Each flow is a sequence of steps that handles device supervision and MDM enrollment.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from apple_device_cli.enrollment.supervised import (
    make_supervised,
    EnrollmentResult,
    erase_device_for_reenrollment,
)
from apple_device_cli.orgs.manager import Organization


@dataclass
class EnrollmentFlow:
    """Base class for enrollment flows."""

    name: str
    description: str


class SimpleSupervisedEnrollment(EnrollmentFlow):
    """Direct device supervision without erase.
    
    Use this for clean devices or when erase is handled separately.
    """
    
    def __init__(self):
        super().__init__(
            name="simple-supervised",
            description="Apply supervision to device without erase"
        )
    
    def execute(
        self,
        org: Organization,
        udid: str,
        skip_list: list[str] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> EnrollmentResult:
        """Apply supervision to a clean device.
        
        Args:
            org: Organization with cert/key
            udid: Device UDID
            skip_list: Setup Assistant panes to skip
            progress_callback: Progress reporting callback
            
        Returns:
            EnrollmentResult with operation details
        """
        return make_supervised(
            cert_path=org.cert_path,  # type: ignore[arg-type]
            key_path=org.key_path,  # type: ignore[arg-type]
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            udid=udid,
            progress_callback=progress_callback,
        )


class ReenrollmentFlow(EnrollmentFlow):
    """Clear cloud config and re-enroll device.
    
    Use when device already has cloud config but needs new organization/MDM.
    """
    
    def __init__(self):
        super().__init__(
            name="reenrollment",
            description="Clear cloud config and re-enroll device"
        )
    
    def execute(
        self,
        org: Organization,
        udid: str,
        skip_list: list[str] | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> EnrollmentResult:
        """Clear cloud config and re-enroll device.
        
        Args:
            org: Organization with cert/key
            udid: Device UDID
            skip_list: Setup Assistant panes to skip
            progress_callback: Progress reporting callback
            
        Returns:
            EnrollmentResult with operation details (success=False if erase failed)
        """
        # Step 1: Erase cloud config
        if progress_callback:
            progress_callback("Clearing cloud configuration...")
        try:
            erase_device_for_reenrollment(udid)
            erase_ok = True
        except Exception as e:
            erase_ok = False
            if progress_callback:
                progress_callback(f"Cloud config erase failed: {e}")
        
        if not erase_ok:
            return EnrollmentResult(
                success=False,
                device_udid=udid,
                errors=["Failed to erase cloud config"],
            )
        
        if progress_callback:
            progress_callback("Waiting for device to reconnect...")
        # Poll for device reconnection instead of fixed sleep - handles fast/slow devices
        device_reconnected = False
        for attempt in range(30):  # 30 seconds max
            try:
                from pymobiledevice3.lockdown import create_using_usbmux
                create_using_usbmux(serial=udid)
                device_reconnected = True
                if progress_callback:
                    progress_callback(f"Device reconnected after {attempt}s")
                break
            except Exception:
                import time as time_module
                time_module.sleep(1)

        if not device_reconnected and progress_callback:
            progress_callback("Device not yet reconnected, proceeding anyway...")
        
        # Step 2: Re-enroll with new org
        if progress_callback:
            progress_callback("Applying new supervision configuration...")
        result = make_supervised(
            cert_path=org.cert_path,  # type: ignore[arg-type]
            key_path=org.key_path,  # type: ignore[arg-type]
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            udid=udid,
            progress_callback=progress_callback,
        )
        
        return result


# Module-level flows dict, initialized lazily to avoid class-level mutable default.
# See flows.py bug: _flows = {} at class level means all subclasses share the same dict
# unless overridden. Use a function-level default instead.
_FLOWS: dict[str, EnrollmentFlow] = {
    "simple-supervised": SimpleSupervisedEnrollment(),
    "reenrollment": ReenrollmentFlow(),
}


class FlowRegistry:
    """Registry of available enrollment flows."""
    
    @classmethod
    def get(cls, name: str) -> Optional[EnrollmentFlow]:
        """Get flow by name."""
        return _FLOWS.get(name)
    
    @classmethod
    def list(cls) -> list[EnrollmentFlow]:
        """List all available flows."""
        return list(_FLOWS.values())
    
    @classmethod
    def register(cls, flow: EnrollmentFlow) -> None:
        """Register a custom flow."""
        _FLOWS[flow.name] = flow
