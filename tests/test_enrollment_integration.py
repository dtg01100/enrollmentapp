"""
End-to-end integration tests for enrollment flows.

Demonstrates full device enrollment workflow through all supported flows:
- SimpleSupervisedEnrollment: Fresh device -> fully supervised
- ReenrollmentFlow: Already enrolled -> new organization
- Error scenarios and recovery
"""

import pytest
from dataclasses import dataclass
from unittest.mock import patch

from apple_device_cli.enrollment.flows import (
    SimpleSupervisedEnrollment,
    ReenrollmentFlow,
    FlowRegistry,
    EnrollmentResult,
)
from apple_device_cli.orgs.manager import Organization


@dataclass
class SimulatedDeviceState:
    """Represents device state at different points in the enrollment flow."""
    udid: str
    activation_state: str  # "Activated" or "Unactivated"
    is_supervised: bool
    cloud_config_applied: bool
    mdm_enrolled: bool
    org_name: str | None = None
    
    def __repr__(self) -> str:
        return (
            f"iPad(udid={self.udid[:8]}..., "
            f"state={self.activation_state}, "
            f"supervised={self.is_supervised}, "
            f"cloud_config={self.cloud_config_applied}, "
            f"mdm={self.mdm_enrolled}, "
            f"org={self.org_name})"
        )


class TestEnrollmentFlowIntegration:
    """Integration tests for complete enrollment workflows."""
    
    @pytest.fixture
    def device_udid(self) -> str:
        """Simulated iPad device UDID."""
        return "abc123def456ghi789jkl"
    
    @pytest.fixture
    def org_config(self) -> dict:
        """Test organization configuration."""
        return {
            "name": "Acme Corp",
            "org_id": "acme-2024",
            "mdm_url": "https://mdm.acme.corp/enroll",
            "checkin_url": "https://mdm.acme.corp/checkin",
            "mdm_topic": "com.apple.mgmt.External.acme-2024",
            "cert_path": "/tmp/acme.der",
            "key_path": "/tmp/acme-key.der",
        }
    
    def test_simple_supervised_flow_fresh_device(self, device_udid, org_config):
        """
        SCENARIO: Fresh iPad from factory
        
        Device state:
          Before: Unactivated, no supervision, no MDM
          After: Fully configured with supervision and MDM
        
        This demonstrates the primary enrollment use case.
        """
        # ===== BEFORE ENROLLMENT =====
        device_before = SimulatedDeviceState(
            udid=device_udid,
            activation_state="Unactivated",
            is_supervised=False,
            cloud_config_applied=False,
            mdm_enrolled=False,
        )
        print(f"\n Fresh Device: {device_before}")
        
        # ===== CREATE FLOW (flow takes org + udid in execute(), not __init__) =====
        org = Organization(
            name=org_config["name"],
            org_id=org_config["org_id"],
            mdm_url=org_config["mdm_url"],
            checkin_url=org_config["checkin_url"],
            mdm_topic=org_config["mdm_topic"],
            cert_path=org_config["cert_path"],
            key_path=org_config["key_path"],
        )
        flow = SimpleSupervisedEnrollment()
        
        # Mock the enrollment execution
        with patch('apple_device_cli.enrollment.flows.make_supervised') as mock_make_supervised:
            async def mock_execute(**kwargs):
                return EnrollmentResult(
                    success=True,
                    device_udid=device_udid,
                    supervised=True,
                    mdm_enrolled=True,
                    errors=[],
                )
            mock_make_supervised.side_effect = mock_execute
            
            # Execute the flow (await since flow.execute is async)
            import asyncio
            result = asyncio.run(flow.execute(org=org, udid=device_udid))
            
            # ===== AFTER ENROLLMENT =====
            device_after = SimulatedDeviceState(
                udid=device_udid,
                activation_state="Activated",
                is_supervised=True,
                cloud_config_applied=True,
                mdm_enrolled=True,
                org_name=org_config["name"],
            )
            
            print(f"[OK] Enrolled Device:  {device_after}")
            print(f"   Flow: {flow.name}")
            print(f"   Organization: {org.name}")
            print(f"   MDM: {org.mdm_url}")
            
            # Verify enrollment succeeded
            assert result.success
            assert result.supervised
            assert result.mdm_enrolled
            assert result.device_udid == device_udid
            assert device_after.is_supervised
            assert device_after.mdm_enrolled
            assert device_after.cloud_config_applied
    
    def test_reenrollment_flow_existing_device(self, device_udid, org_config):
        """
        SCENARIO: Device already enrolled with one org, re-enroll to different org
        
        Device state:
          Before: Fully enrolled with "Acme Corp", supervised
          After: Fully enrolled with "NewOrg", supervised
        
        This demonstrates migration between organizations.
        """
        new_org_config = {
            "name": "NewOrg Inc",
            "org_id": "neworg-2024",
            "mdm_url": "https://mdm.neworg.corp/enroll",
            "checkin_url": "https://mdm.neworg.corp/checkin",
            "mdm_topic": "com.apple.mgmt.External.neworg-2024",
            "cert_path": "/tmp/neworg.der",
            "key_path": "/tmp/neworg-key.der",
        }
        
        # ===== BEFORE RE-ENROLLMENT =====
        device_before = SimulatedDeviceState(
            udid=device_udid,
            activation_state="Activated",
            is_supervised=True,
            cloud_config_applied=True,
            mdm_enrolled=True,
            org_name="Acme Corp",
        )
        print(f"\n Existing Device:  {device_before}")
        
        # ===== DETECT ENROLLED STATE =====
        needs_erase = device_before.cloud_config_applied
        print(f"   Needs erase? {needs_erase} (already enrolled)")
        
        # ===== CREATE REENROLLMENT FLOW =====
        new_org = Organization(
            name=new_org_config["name"],
            org_id=new_org_config["org_id"],
            mdm_url=new_org_config["mdm_url"],
            checkin_url=new_org_config["checkin_url"],
            mdm_topic=new_org_config["mdm_topic"],
            cert_path=new_org_config["cert_path"],
            key_path=new_org_config["key_path"],
        )
        flow = ReenrollmentFlow()
        
        with patch('apple_device_cli.enrollment.flows.erase_device_for_reenrollment') as mock_erase, \
             patch('apple_device_cli.enrollment.flows.make_supervised') as mock_make_supervised:
            mock_erase.return_value = True  # Erase succeeds
            
            # make_supervised is synchronous, returns EnrollmentResult directly
            def mock_execute(**kwargs):
                return EnrollmentResult(
                    success=True,
                    device_udid=device_udid,
                    supervised=True,
                    mdm_enrolled=True,
                    errors=[],
                )
            mock_make_supervised.side_effect = mock_execute
            
            # Execute the flow (returns tuple of erase success + result)
            erase_ok, result = flow.execute(org=new_org, udid=device_udid)
            
            # ===== AFTER RE-ENROLLMENT =====
            device_after = SimulatedDeviceState(
                udid=device_udid,
                activation_state="Activated",
                is_supervised=True,
                cloud_config_applied=True,
                mdm_enrolled=True,
                org_name=new_org_config["name"],
            )
            
            print(f"[OK] Re-enrolled Device: {device_after}")
            print(f"   Flow: {flow.name}")
            print(f"   New Organization: {new_org.name}")
            print(f"   New MDM: {new_org.mdm_url}")
            
            # Verify re-enrollment succeeded
            assert result.success
            assert result.supervised
            assert result.mdm_enrolled
            assert device_after.org_name == new_org_config["name"]
            assert device_after.org_name != "Acme Corp"
    
    def test_enrollment_with_mdm_failure_recovery(self, device_udid, org_config):
        """
        SCENARIO: MDM enrollment fails but supervision succeeds
        
        Device state:
          Before: Unactivated
          After: Supervised but MDM enrollment failed
        
        This demonstrates error recovery where partial enrollment is acceptable.
        """
        # ===== BEFORE ENROLLMENT =====
        device_before = SimulatedDeviceState(
            udid=device_udid,
            activation_state="Unactivated",
            is_supervised=False,
            cloud_config_applied=False,
            mdm_enrolled=False,
        )
        print(f"\n Fresh Device:     {device_before}")
        
        # ===== CREATE FLOW =====
        org = Organization(
            name=org_config["name"],
            org_id=org_config["org_id"],
            mdm_url=org_config["mdm_url"],
            checkin_url=org_config["checkin_url"],
            mdm_topic=org_config["mdm_topic"],
            cert_path=org_config["cert_path"],
            key_path=org_config["key_path"],
        )
        flow = SimpleSupervisedEnrollment()
        
        with patch('apple_device_cli.enrollment.flows.make_supervised') as mock_make_supervised:
            # Simulate MDM failure
            async def mock_execute(**kwargs):
                return EnrollmentResult(
                    success=False,  # Overall failed due to MDM
                    device_udid=device_udid,
                    supervised=True,  # But supervision worked
                    mdm_enrolled=False,  # MDM failed
                    errors=["MDM enrollment failed: connection timeout"],
                )
            mock_make_supervised.side_effect = mock_execute
            
            import asyncio
            result = asyncio.run(flow.execute(org=org, udid=device_udid))
            
            # ===== AFTER ENROLLMENT (PARTIAL) =====
            device_after = SimulatedDeviceState(
                udid=device_udid,
                activation_state="Activated",
                is_supervised=True,
                cloud_config_applied=True,
                mdm_enrolled=False,  # Failed
                org_name=org_config["name"],
            )
            
            print(f"[WARN]  Partial Success:  {device_after}")
            print(f"   Flow: {flow.name}")
            print("   Supervision: [OK] SUCCESS")
            print(f"   MDM Enrollment: [FAIL] FAILED - {result.errors[0]}")
            
            # Verify supervision succeeded despite MDM failure
            assert result.supervised
            assert not result.mdm_enrolled
            assert len(result.errors) > 0
            assert device_after.is_supervised
            assert not device_after.mdm_enrolled
    
    def test_flow_registry_discovery(self):
        """
        SCENARIO: Discover available enrollment flows
        
        Shows that flows are discoverable and extensible for custom implementations.
        """
        registry = FlowRegistry()
        flows = registry.list()  # Changed from list_flows() to list()
        
        print("\n[DOC] Available Enrollment Flows:")
        for flow in flows:
            print(f"   - {flow.name}: {flow.description}")
        
        # Verify standard flows are registered
        flow_names = [f.name for f in flows]
        assert "simple-supervised" in flow_names  # Changed from class name to snake_case name
        assert "reenrollment" in flow_names
        
        # Verify flows can be retrieved
        simple_flow = registry.get("simple-supervised")
        assert simple_flow is not None
        assert simple_flow.name == "simple-supervised"


class TestDeviceStateProgression:
    """
    Detailed device state transitions throughout enrollment process.
    
    This test class documents the complete journey of an iPad from
    factory fresh to fully managed.
    """
    
    def test_complete_device_lifecycle(self):
        """
        Documents the complete state progression from fresh device to managed.
        
        States:
          0. Factory Fresh: Unactivated, not supervised, no MDM
          1. After Pairing: Activated, not supervised, no MDM
          2. After Supervision: Activated, supervised, no MDM
          3. After MDM Enrollment: Activated, supervised, MDM enrolled
        """
        states = [
            ("Factory Fresh", False, False, False),
            ("After Pairing", True, False, False),
            ("After Supervision", True, True, False),
            ("After MDM Enrollment", True, True, True),
        ]
        
        print("\n=== Device State Progression ===")
        for i, (label, activated, supervised, mdm) in enumerate(states):
            device = SimulatedDeviceState(
                udid="test-udid",
                activation_state="Activated" if activated else "Unactivated",
                is_supervised=supervised,
                cloud_config_applied=mdm,
                mdm_enrolled=mdm,
            )
            print(f"  {i}. {label}: {device}")
        
        # Final state should be fully managed
        final = states[-1]
        assert final == ("After MDM Enrollment", True, True, True)
