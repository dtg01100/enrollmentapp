import pytest
import tempfile
from pathlib import Path
from apple_device_cli.orgs.manager import Organization, OrganizationManager


def test_organization_to_dict():
    org = Organization(name="Test Org", org_id="com.test")
    data = org.to_dict()
    assert data["name"] == "Test Org"
    assert data["org_id"] == "com.test"


def test_organization_from_dict():
    data = {"name": "Test Org", "org_id": "com.test", "address": None, "phone": None, "email": None, "cert_path": None, "key_path": None, "created_at": "2024-01-01T00:00:00"}
    org = Organization.from_dict(data)
    assert org.name == "Test Org"
    assert org.org_id == "com.test"


def test_organization_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        org = Organization(name="Test Org", org_id="com.test", address="123 Main St")
        org.save(Path(tmpdir))
        loaded = Organization.load(Path(tmpdir))
        assert loaded.name == "Test Org"
        assert loaded.org_id == "com.test"
        assert loaded.address == "123 Main St"


def test_organization_manager_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        orgs = manager.list_orgs()
        assert orgs == []


def test_organization_manager_save_and_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org = Organization(name="Test Org", org_id="com.test")
        manager.save_org(org)
        orgs = manager.list_orgs()
        assert len(orgs) == 1
        assert orgs[0].name == "Test Org"


def test_organization_manager_delete():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org = Organization(name="Test Org", org_id="com.test")
        manager.save_org(org)
        assert manager.delete_org("Test Org") == True
        assert manager.delete_org("NonExistent") == False


def test_organization_manager_get_org():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org = Organization(name="Test Org", org_id="com.test")
        manager.save_org(org)
        retrieved = manager.get_org("Test Org")
        assert retrieved is not None
        assert retrieved.name == "Test Org"
        assert retrieved.org_id == "com.test"