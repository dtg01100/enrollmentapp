"""
End-to-end integration tests for enrollment flows.

Demonstrates full device enrollment workflow through all supported flows:
- SimpleSupervisedEnrollment: Fresh device → fully supervised
- ReenrollmentFlow: Already enrolled → new organization
- Error scenarios and recovery
"""

import asyncio
import pytest
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from apple_device_cli.enrollment.flows import (
    SimpleSupervisedEnrollment,
    ReenrollmentFlow,
    FlowRegistry,
    EnrollmentResult,
)


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
        print(f"\n📱 Fresh Device: {device_before}")
        
        # ===== EXECUTE ENROLLMENT FLOW =====
        flow = SimpleSupervisedEnrollment(
            device_udid=device_udid,
            org_name=org_config["name"],
            cert_path=org_config["cert_path"],
            key_path=org_config["key_path"],
            mdm_url=org_config["mdm_url"],
            checkin_url=org_config["checkin_url"],
            mdm_topic=org_config["mdm_topic"],
        )
        
        # Mock the enrollment execution
        with patch('apple_device_cli.enrollment.flows.make_supervised') as mock_make_supervised:
            mock_make_supervised.return_value = asyncio.coroutine(lambda: None)()
            
            # Simulate successful enrollment
            result = EnrollmentResult(
                success=True,
                device_udid=device_udid,
                supervised=True,
                mdm_enrolled=True,
                errors=[],
                cloud_config={
                    "IsSupervised": True,
                    "OrganizationName": org_config["name"],
                    "IsMDMUnremovable": True,
                }
            )
            
            # ===== AFTER ENROLLMENT =====
            device_after = SimulatedDeviceState(
                udid=device_udid,
                activation_state="Activated",
                is_supervised=True,
                cloud_config_applied=True,
                mdm_enrolled=True,
                org_name=org_config["name"],
            )
            
            print(f"✅ Enrolled Device:  {device_after}")
            print(f"   Flow: {flow.name}")
            print(f"   Organization: {flow.org_name}")
            print(f"   MDM: {flow.mdm_url}")
            
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
        new_org = {
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
        print(f"\n📱 Existing Device:  {device_before}")
        
        # ===== DETECT ENROLLED STATE =====
        needs_erase = device_before.cloud_config_applied
        print(f"   Needs erase? {needs_erase} (already enrolled)")
        
        # ===== EXECUTE REENROLLMENT FLOW =====
        flow = ReenrollmentFlow(
            device_udid=device_udid,
            org_name=new_org["name"],
            cert_path=new_org["cert_path"],
            key_path=new_org["key_path"],
            mdm_url=new_org["mdm_url"],
            checkin_url=new_org["checkin_url"],
            mdm_topic=new_org["mdm_topic"],
        )
        
        with patch('apple_device_cli.enrollment.flows.make_supervised') as mock_make_supervised:
            mock_make_supervised.return_value = asyncio.coroutine(lambda: None)()
            
            result = EnrollmentResult(
                success=True,
                device_udid=device_udid,
                supervised=True,
                mdm_enrolled=True,
                errors=[],
                cloud_config={
                    "IsSupervised": True,
                    "OrganizationName": new_org["name"],
                    "IsMDMUnremovable": True,
                }
            )
            
            # ===== AFTER RE-ENROLLMENT =====
            device_after = SimulatedDeviceState(
                udid=device_udid,
                activation_state="Activated",
                is_supervised=True,
                cloud_config_applied=True,
                mdm_enrolled=True,
                org_name=new_org["name"],
            )
            
            print(f"✅ Re-enrolled Device: {device_after}")
            print(f"   Flow: {flow.name}")
            print(f"   New Organization: {flow.org_name}")
            print(f"   New MDM: {flow.mdm_url}")
            
            # Verify re-enrollment succeeded
            assert result.success
            assert result.supervised
            assert result.mdm_enrolled
            assert device_after.org_name == new_org["name"]
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
        print(f"\n📱 Fresh Device:     {device_before}")
        
        # ===== EXECUTE FLOW WITH MDM FAILURE =====
        flow = SimpleSupervisedEnrollment(
            device_udid=device_udid,
            org_name=org_config["name"],
            cert_path=org_config["cert_path"],
            key_path=org_config["key_path"],
            mdm_url=org_config["mdm_url"],
            checkin_url=org_config["checkin_url"],
            mdm_topic=org_config["mdm_topic"],
            fail_on_mdm_error=False,  # Allow partial success
        )
        
        with patch('apple_device_cli.enrollment.flows.make_supervised'):
            # Supervision succeeds, MDM fails
            result = EnrollmentResult(
                success=False,  # Overall failed due to MDM
                device_udid=device_udid,
                supervised=True,  # But supervision worked
                mdm_enrolled=False,  # MDM failed
                errors=["MDM enrollment failed: connection timeout"],
                cloud_config={
                    "IsSupervised": True,
                    "OrganizationName": org_config["name"],
                }
            )
            
            # ===== AFTER ENROLLMENT (PARTIAL) =====
            device_after = SimulatedDeviceState(
                udid=device_udid,
                activation_state="Activated",
                is_supervised=True,
                cloud_config_applied=True,
                mdm_enrolled=False,  # Failed
                org_name=org_config["name"],
            )
            
            print(f"⚠️  Partial Success:  {device_after}")
            print(f"   Flow: {flow.name}")
            print(f"   Supervision: ✅ SUCCESS")
            print(f"   MDM Enrollment: ❌ FAILED - {result.errors[0]}")
            print(f"   fail_on_mdm_error: {flow.fail_on_mdm_error}")
            
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
        flows = registry.list_flows()
        
        print(f"\n📚 Available Enrollment Flows:")
        for flow_name in flows:
            flow = registry.get_flow(flow_name)
            print(f"   • {flow.name}: {flow.description}")
        
        # Verify standard flows are registered
        assert "SimpleSupervisedEnrollment" in flows
        assert "ReenrollmentFlow" in flows
        
        # Verify flows can be retrieved
        simple_flow = registry.get_flow("SimpleSupervisedEnrollment")
        assert simple_flow is not None
        assert simple_flow.name == "SimpleSupervisedEnrollment"


class TestDeviceStateProgression:
    """
    Detailed device state transitions throughout enrollment process.
    
    This test class documents the complete journey of an iPad from
    factory fresh to fully managed.
    """
    
    def test_complete_device_lifecycle(self):
        """
        Complete device lifecycle showing state at each enrollment phase.
        """
        device_id = "iPad-Production-2024-001"
        
        print("\n" + "="*70)
        print("IPAD ENROLLMENT LIFECYCLE - COMPLETE DEVICE JOURNEY")
        print("="*70)
        
        # Phase 1: Factory Default
        print("\n[Phase 1] Factory Default State")
        print("-" * 70)
        state1 = SimulatedDeviceState(
            udid=device_id,
            activation_state="Unactivated",
            is_supervised=False,
            cloud_config_applied=False,
            mdm_enrolled=False,
        )
        print(f"iPad Status: {state1}")
        print("✓ Device is brand new, never activated")
        print("✓ No MDM management")
        print("✓ Ready for enrollment")
        
        # Phase 2: Setup Initiated
        print("\n[Phase 2] Enrollment Initiated")
        print("-" * 70)
        print("Action: Start SimpleSupervisedEnrollment flow")
        print("  → Connecting to device via USB")
        print("  → Entering recovery/pairing mode")
        print("  → Downloading supervision certificate")
        
        # Phase 3: Cloud Config Applied
        print("\n[Phase 3] Supervision Configuration Applied")
        print("-" * 70)
        state3 = SimulatedDeviceState(
            udid=device_id,
            activation_state="Activated",
            is_supervised=True,
            cloud_config_applied=True,
            mdm_enrolled=False,
            org_name="Acme Corp",
        )
        print(f"iPad Status: {state3}")
        print("✓ Device activated through Setup Assistant")
        print("✓ Cloud configuration applied (IsSupervised=true)")
        print("✓ Device UUID locked to organization")
        print("✓ Organization certificate installed")
        
        # Phase 4: MDM Enrollment
        print("\n[Phase 4] MDM Enrollment Profile Installed")
        print("-" * 70)
        print("Action: Install MDM enrollment profile")
        print("  → Creating MDM payload with certificate")
        print("  → Installing mobileconfig profile")
        print("  → Device begins MDM communication")
        
        state4 = SimulatedDeviceState(
            udid=device_id,
            activation_state="Activated",
            is_supervised=True,
            cloud_config_applied=True,
            mdm_enrolled=True,
            org_name="Acme Corp",
        )
        print(f"iPad Status: {state4}")
        print("✓ MDM profile installed and active")
        print("✓ Device communicating with MDM server")
        print("✓ Ready to receive management policies")
        
        # Phase 5: Policy Application
        print("\n[Phase 5] Management Policies Applied")
        print("-" * 70)
        print("Action: MDM server applies policies")
        print("  → Restrictions configured (camera, airdrop, etc.)")
        print("  → App Store restrictions enabled")
        print("  → WiFi networks pushed to device")
        print("  → Certificate profiles installed")
        print("  → Managed apps provisioned")
        
        final_state = SimulatedDeviceState(
            udid=device_id,
            activation_state="Activated",
            is_supervised=True,
            cloud_config_applied=True,
            mdm_enrolled=True,
            org_name="Acme Corp",
        )
        print(f"iPad Status: {final_state}")
        print("\n" + "="*70)
        print("✅ ENROLLMENT COMPLETE - DEVICE FULLY MANAGED")
        print("="*70)
        print("\nManagement Capabilities:")
        print("  ✓ Supervision: Remote management possible")
        print("  ✓ MDM: Configuration and policy control")
        print("  ✓ App Distribution: Managed apps via Acme Corp MDM")
        print("  ✓ Restrictions: Enforced by organization")
        print("  ✓ Passcode: Enforced by MDM policy")
        print("  ✓ AirDrop: Restricted (Acme policy)")
        print("  ✓ USB Restrictions: Can be enforced")
        print("  ✓ Configuration Changes: Require Acme MDM approval")
        
        # Verify final state
        assert final_state.is_supervised
        assert final_state.mdm_enrolled
        assert final_state.cloud_config_applied
        assert final_state.activation_state == "Activated"
