"""Tests for org set-* commands that update existing organizations.

These tests verify that the set-* commands correctly update existing organizations
by testing the underlying OrganizationManager.save_org with overwrite=True.
"""
import pytest
import tempfile
from pathlib import Path

from apple_device_cli.orgs.manager import Organization, OrganizationManager


class TestOrgSetCommands:
    """Tests for org set-* command logic via OrganizationManager."""
    
    def test_save_org_with_overwrite_updates_mdm_url(self):
        """Test that updating an org's MDM URL works with overwrite=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OrganizationManager(Path(tmpdir))
            
            # Create initial org
            org = Organization(name="Test Org", mdm_url="https://old.example.com")
            manager.save_org(org)
            
            # Update MDM URL
            org.mdm_url = "https://new.example.com"
            manager.save_org(org, overwrite=True)
            
            # Verify
            updated = manager.get_org("Test Org")
            assert updated.mdm_url == "https://new.example.com"
    
    def test_save_org_with_overwrite_updates_checkin_url(self):
        """Test that updating an org's check-in URL works with overwrite=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OrganizationManager(Path(tmpdir))
            
            org = Organization(name="Test Org")
            manager.save_org(org)
            
            org.checkin_url = "https://checkin.example.com/checkin"
            manager.save_org(org, overwrite=True)
            
            updated = manager.get_org("Test Org")
            assert updated.checkin_url == "https://checkin.example.com/checkin"
    
    def test_save_org_with_overwrite_updates_mdm_topic(self):
        """Test that updating an org's MDM topic works with overwrite=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OrganizationManager(Path(tmpdir))
            
            org = Organization(name="Test Org")
            manager.save_org(org)
            
            org.mdm_topic = "com.new.topic"
            manager.save_org(org, overwrite=True)
            
            updated = manager.get_org("Test Org")
            assert updated.mdm_topic == "com.new.topic"
    
    def test_save_org_with_overwrite_updates_multiple_fields(self):
        """Test that updating multiple MDM fields at once works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OrganizationManager(Path(tmpdir))
            
            org = Organization(name="Test Org")
            manager.save_org(org)
            
            # Update multiple fields
            org.mdm_url = "https://mdm.example.com/mdm"
            org.checkin_url = "https://mdm.example.com/checkin"
            org.mdm_topic = "com.example.topic"
            org.mdm_description = "Test MDM Provider"
            manager.save_org(org, overwrite=True)
            
            updated = manager.get_org("Test Org")
            assert updated.mdm_url == "https://mdm.example.com/mdm"
            assert updated.checkin_url == "https://mdm.example.com/checkin"
            assert updated.mdm_topic == "com.example.topic"
            assert updated.mdm_description == "Test MDM Provider"
    
    def test_save_org_without_overwrite_raises_on_existing(self):
        """Test that updating without overwrite=True raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = OrganizationManager(Path(tmpdir))
            
            org = Organization(name="Test Org")
            manager.save_org(org)
            
            # Try to update without overwrite flag
            org.mdm_url = "https://new.example.com"
            with pytest.raises(ValueError, match="already exists"):
                manager.save_org(org, overwrite=False)
    
    def test_all_mdm_fields_preserved_through_save_load_cycle(self):
        """Test that all MDM fields survive a save/load cycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            org = Organization(
                name="Test Org",
                org_id="com.test",
                mdm_url="https://mdm.example.com/mdm",
                checkin_url="https://mdm.example.com/checkin",
                mdm_topic="com.test.topic",
                identity_ref="cert-uuid-123",
                mdm_description="Test Provider",
            )
            
            # Save to directory
            save_dir = Path(tmpdir) / "test_org"
            save_dir.mkdir()
            org.save(save_dir)
            
            # Load back
            loaded = Organization.load(save_dir)
            
            assert loaded.name == "Test Org"
            assert loaded.org_id == "com.test"
            assert loaded.mdm_url == "https://mdm.example.com/mdm"
            assert loaded.checkin_url == "https://mdm.example.com/checkin"
            assert loaded.mdm_topic == "com.test.topic"
            assert loaded.identity_ref == "cert-uuid-123"
            assert loaded.mdm_description == "Test Provider"


class TestOrganizationJsonRoundtrip:
    """Tests for Organization JSON serialization/deserialization."""
    
    def test_to_dict_includes_all_mdm_fields(self):
        """Test that to_dict includes all MDM-related fields."""
        org = Organization(
            name="Test",
            mdm_url="https://mdm.test.com",
            checkin_url="https://checkin.test.com",
            mdm_topic="com.test.topic",
            identity_ref="ref-123",
            mdm_description="Test Desc",
        )
        data = org.to_dict()
        
        assert data["mdm_url"] == "https://mdm.test.com"
        assert data["checkin_url"] == "https://checkin.test.com"
        assert data["mdm_topic"] == "com.test.topic"
        assert data["identity_ref"] == "ref-123"
        assert data["mdm_description"] == "Test Desc"
    
    def test_from_dict_loads_all_mdm_fields(self):
        """Test that from_dict correctly loads all MDM-related fields."""
        data = {
            "name": "Test",
            "org_id": "com.test",
            "mdm_url": "https://mdm.test.com",
            "checkin_url": "https://checkin.test.com",
            "mdm_topic": "com.test.topic",
            "identity_ref": "ref-123",
            "mdm_description": "Test Desc",
            "address": None,
            "phone": None,
            "email": None,
            "cert_path": None,
            "key_path": None,
            "created_at": "2024-01-01T00:00:00",
        }
        org = Organization.from_dict(data)
        
        assert org.mdm_url == "https://mdm.test.com"
        assert org.checkin_url == "https://checkin.test.com"
        assert org.mdm_topic == "com.test.topic"
        assert org.identity_ref == "ref-123"
        assert org.mdm_description == "Test Desc"
    
    def test_from_dict_handles_missing_optional_fields(self):
        """Test that from_dict handles missing optional fields gracefully."""
        data = {
            "name": "Test",
            "created_at": "2024-01-01T00:00:00",
        }
        org = Organization.from_dict(data)
        
        assert org.name == "Test"
        assert org.mdm_url is None
        assert org.checkin_url is None
        assert org.mdm_topic is None
        assert org.identity_ref is None
        assert org.mdm_description is None