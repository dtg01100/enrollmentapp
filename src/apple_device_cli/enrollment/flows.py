"""High-level enrollment flow orchestration.

This module provides reusable enrollment flows that can be called from CLI or other UIs.
Each flow is a sequence of steps that handles device supervision and MDM enrollment.
"""
from __future__ import annotations

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
            cert_path=org.cert_path,
            key_path=org.key_path,
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
    ) -> tuple[bool, EnrollmentResult]:
        """Clear cloud config and re-enroll device.
        
        Args:
            org: Organization with cert/key
            udid: Device UDID
            skip_list: Setup Assistant panes to skip
            progress_callback: Progress reporting callback
            
        Returns:
            Tuple of (erase_succeeded, enrollment_result)
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
            return (False, EnrollmentResult(
                success=False,
                device_udid=udid,
                errors=["Failed to erase cloud config"],
            ))
        
        if progress_callback:
            progress_callback("Waiting 30s for device reset...")
        import time
        time.sleep(30)
        
        # Step 2: Re-enroll with new org
        if progress_callback:
            progress_callback("Applying new supervision configuration...")
        result = make_supervised(
            cert_path=org.cert_path,
            key_path=org.key_path,
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            udid=udid,
            progress_callback=progress_callback,
        )
        
        return (True, result)


class FlowRegistry:
    """Registry of available enrollment flows."""
    
    _flows = {
        "simple-supervised": SimpleSupervisedEnrollment(),
        "reenrollment": ReenrollmentFlow(),
    }
    
    @classmethod
    def get(cls, name: str) -> Optional[EnrollmentFlow]:
        """Get flow by name."""
        return cls._flows.get(name)
    
    @classmethod
    def list(cls) -> list[EnrollmentFlow]:
        """List all available flows."""
        return list(cls._flows.values())
    
    @classmethod
    def register(cls, flow: EnrollmentFlow) -> None:
        """Register a custom flow."""
        cls._flows[flow.name] = flow
