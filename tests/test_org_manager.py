import pytest
import tempfile
from pathlib import Path
from apple_device_cli.orgs.manager import Organization, OrganizationManager


def test_organization_to_dict():
    org = Organization(name="Test Org", org_id="com.test")
    data = org.to_dict()
    assert data["name"] == "Test Org"
    assert data["org_id"] == "com.test"


def test_organization_to_dict_includes_mdm_fields():
    """Test that to_dict includes all MDM-related fields."""
    org = Organization(
        name="Test Org",
        org_id="com.test",
        mdm_url="https://mdm.example.com",
        checkin_url="https://mdm.example.com/checkin",
        mdm_topic="com.test.topic",
        mdm_description="Test MDM",
        identity_ref="cert-uuid-123",
    )
    data = org.to_dict()
    assert data["mdm_url"] == "https://mdm.example.com"
    assert data["checkin_url"] == "https://mdm.example.com/checkin"
    assert data["mdm_topic"] == "com.test.topic"
    assert data["mdm_description"] == "Test MDM"
    assert data["identity_ref"] == "cert-uuid-123"


def test_organization_from_dict():
    data = {"name": "Test Org", "org_id": "com.test", "address": None, "phone": None, "email": None, "cert_path": None, "key_path": None, "created_at": "2024-01-01T00:00:00"}
    org = Organization.from_dict(data)
    assert org.name == "Test Org"
    assert org.org_id == "com.test"


def test_organization_from_dict_with_mdm_fields():
    """Test that from_dict properly loads all MDM-related fields."""
    data = {
        "name": "Test Org",
        "org_id": "com.test",
        "mdm_url": "https://mdm.example.com",
        "checkin_url": "https://mdm.example.com/checkin",
        "mdm_topic": "com.test.topic",
        "mdm_description": "Test MDM",
        "identity_ref": "cert-uuid-123",
        "address": None,
        "phone": None,
        "email": None,
        "cert_path": None,
        "key_path": None,
        "created_at": "2024-01-01T00:00:00",
    }
    org = Organization.from_dict(data)
    assert org.name == "Test Org"
    assert org.mdm_url == "https://mdm.example.com"
    assert org.checkin_url == "https://mdm.example.com/checkin"
    assert org.mdm_topic == "com.test.topic"
    assert org.mdm_description == "Test MDM"
    assert org.identity_ref == "cert-uuid-123"


def test_organization_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        org = Organization(name="Test Org", org_id="com.test", address="123 Main St")
        org.save(Path(tmpdir))
        loaded = Organization.load(Path(tmpdir))
        assert loaded.name == "Test Org"
        assert loaded.org_id == "com.test"
        assert loaded.address == "123 Main St"


def test_organization_save_load_with_mdm_fields():
    """Test that save/load roundtrip preserves all MDM fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        org = Organization(
            name="Test Org",
            org_id="com.test",
            address="123 Main St",
            mdm_url="https://mdm.example.com/mdm",
            checkin_url="https://mdm.example.com/checkin",
            mdm_topic="com.test.topic",
            mdm_description="Test MDM Provider",
            identity_ref="cert-uuid-123",
        )
        org.save(Path(tmpdir))
        loaded = Organization.load(Path(tmpdir))
        
        assert loaded.name == "Test Org"
        assert loaded.mdm_url == "https://mdm.example.com/mdm"
        assert loaded.checkin_url == "https://mdm.example.com/checkin"
        assert loaded.mdm_topic == "com.test.topic"
        assert loaded.mdm_description == "Test MDM Provider"
        assert loaded.identity_ref == "cert-uuid-123"


def test_organization_save_load_with_cert_and_key():
    """Test that save/load handles cert_path and key_path correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock cert and key files
        cert_path = Path(tmpdir) / "test_cert.der"
        key_path = Path(tmpdir) / "test_key.der"
        cert_path.write_bytes(b"mock cert")
        key_path.write_bytes(b"mock key")
        
        org = Organization(
            name="Test Org",
            cert_path=str(cert_path),
            key_path=str(key_path),
        )
        # Save to a subdirectory
        save_dir = Path(tmpdir) / "saved_org"
        save_dir.mkdir()
        org.save(save_dir)
        
        # Verify cert and key were copied
        assert (save_dir / "cert.der").exists()
        assert (save_dir / "key.der").exists()
        
        # Load and verify paths are updated to the saved location
        loaded = Organization.load(save_dir)
        assert loaded.cert_path == str(save_dir / "cert.der")
        assert loaded.key_path == str(save_dir / "key.der")


def test_organization_save_load_skip_copy_preserves_files():
    """Test that skip_copy=True does NOT copy cert/key but they can still be loaded if already in place."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock cert and key files in external location
        cert_path = Path(tmpdir) / "external_cert.der"
        key_path = Path(tmpdir) / "external_key.der"
        cert_path.write_bytes(b"mock cert")
        key_path.write_bytes(b"mock key")
        
        org = Organization(
            name="Test Org",
            cert_path=str(cert_path),
            key_path=str(key_path),
        )
        save_dir = Path(tmpdir) / "saved_org"
        save_dir.mkdir()
        org.save(save_dir, skip_copy=True)
        
        # Verify cert and key were NOT copied to save_dir
        assert not (save_dir / "cert.der").exists()
        assert not (save_dir / "key.der").exists()
        
        # Verify org.json was saved (and does NOT include cert_path/key_path)
        import json
        with open(save_dir / "org.json") as f:
            data = json.load(f)
        assert data["name"] == "Test Org"
        assert "cert_path" not in data  # cert_path is deliberately omitted
        assert "key_path" not in data  # key_path is deliberately omitted
        
        # Load returns None for cert/key since files are not in org_dir
        loaded = Organization.load(save_dir)
        assert loaded.cert_path is None
        assert loaded.key_path is None


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
        assert manager.delete_org("Test Org")
        assert not manager.delete_org("NonExistent")


def test_organization_manager_get_org():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org = Organization(name="Test Org", org_id="com.test")
        manager.save_org(org)
        retrieved = manager.get_org("Test Org")
        assert retrieved is not None
        assert retrieved.name == "Test Org"
        assert retrieved.org_id == "com.test"


def test_sanitize_name_special_chars():
    """_sanitize_name should convert special characters to underscores."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        sanitized = manager._sanitize_name("Test/Org:With<Special>Chars")
        assert sanitized == "Test_Org_With_Special_Chars"


def test_sanitize_name_preserves_alphanumeric():
    """_sanitize_name should preserve alphanumeric, dots, hyphens, underscores."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        assert manager._sanitize_name("Test-Org_123.example") == "Test-Org_123.example"


def test_save_org_overwrite():
    """save_org with overwrite=True should replace existing org."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org1 = Organization(name="Test Org", org_id="com.test.v1")
        manager.save_org(org1)
        
        org2 = Organization(name="Test Org", org_id="com.test.v2")
        manager.save_org(org2, overwrite=True)
        
        retrieved = manager.get_org("Test Org")
        assert retrieved.org_id == "com.test.v2"


def test_save_org_no_overwrite_raises():
    """save_org without overwrite should raise when org exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org1 = Organization(name="Test Org", org_id="com.test.v1")
        manager.save_org(org1)
        
        org2 = Organization(name="Test Org", org_id="com.test.v2")
        with pytest.raises(ValueError, match="already exists"):
            manager.save_org(org2, overwrite=False)


def test_save_org_updates_existing_org():
    """Test that save_org with overwrite=True updates an existing org's MDM fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        
        # Create initial org
        org1 = Organization(name="Test Org", org_id="com.test.v1", mdm_url="https://old.example.com")
        manager.save_org(org1)
        
        # Update with new MDM settings
        org2 = Organization(name="Test Org", org_id="com.test.v2", mdm_url="https://new.example.com")
        manager.save_org(org2, overwrite=True)
        
        # Verify update
        retrieved = manager.get_org("Test Org")
        assert retrieved.mdm_url == "https://new.example.com"
        assert retrieved.org_id == "com.test.v2"


def test_get_org_with_mdm_fields():
    """Test that get_org retrieves all MDM fields correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org = Organization(
            name="Test Org",
            org_id="com.test",
            mdm_url="https://mdm.example.com",
            checkin_url="https://mdm.example.com/checkin",
            mdm_topic="com.test.topic",
        )
        manager.save_org(org)
        
        retrieved = manager.get_org("Test Org")
        assert retrieved is not None
        assert retrieved.mdm_url == "https://mdm.example.com"
        assert retrieved.checkin_url == "https://mdm.example.com/checkin"
        assert retrieved.mdm_topic == "com.test.topic"


def test_list_orgs_includes_mdm_fields():
    """Test that list_orgs returns orgs with MDM fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        
        org1 = Organization(name="Org1", mdm_url="https://org1.example.com")
        org2 = Organization(name="Org2", mdm_url="https://org2.example.com")
        
        manager.save_org(org1)
        manager.save_org(org2)
        
        orgs = manager.list_orgs()
        assert len(orgs) == 2
        
        # Verify both have MDM URLs
        mdm_urls = {o.mdm_url for o in orgs}
        assert "https://org1.example.com" in mdm_urls
        assert "https://org2.example.com" in mdm_urls


def test_delete_org_removes_all_files():
    """Test that delete_org removes the org directory completely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        
        # Create org with mock cert/key files
        cert_path = Path(tmpdir) / "cert.der"
        key_path = Path(tmpdir) / "key.der"
        cert_path.write_bytes(b"cert")
        key_path.write_bytes(b"key")
        
        org = Organization(name="Test Org", cert_path=str(cert_path), key_path=str(key_path))
        manager.save_org(org)
        
        # Verify org exists
        assert manager.get_org("Test Org") is not None
        
        # Delete
        result = manager.delete_org("Test Org")
        assert result is True
        
        # Verify deleted
        assert manager.get_org("Test Org") is None
        assert not (manager.orgs_dir / "Test_Org").exists()


def test_export_org_to_directory():
    """Test that export_org to directory preserves all MDM fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        
        org = Organization(
            name="Test Org",
            org_id="com.test",
            mdm_url="https://mdm.example.com",
            checkin_url="https://mdm.example.com/checkin",
            mdm_topic="com.test.topic",
        )
        manager.save_org(org)
        
        # Export to directory
        export_dir = Path(tmpdir) / "exported"
        result = manager.export_org("Test Org", export_dir)
        assert result is True
        
        # Export creates subdirectory with sanitized org name: exported/Test_Org/
        org_export_dir = export_dir / "Test_Org"
        assert (org_export_dir / "org.json").exists()
        
        # Load and verify MDM fields
        loaded = Organization.load(org_export_dir)
        assert loaded.mdm_url == "https://mdm.example.com"
        assert loaded.checkin_url == "https://mdm.example.com/checkin"
        assert loaded.mdm_topic == "com.test.topic"


def test_export_org_to_zip():
    """Test that export_org to zip preserves all MDM fields."""
    import zipfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        
        org = Organization(
            name="Test Org",
            org_id="com.test",
            mdm_url="https://mdm.example.com",
        )
        manager.save_org(org)
        
        # Export to zip
        zip_path = Path(tmpdir) / "exported.zip"
        result = manager.export_org("Test Org", zip_path)
        assert result is True
        assert zip_path.exists()
        
        # Verify zip contents
        with zipfile.ZipFile(zip_path, 'r') as zf:
            assert "org.json" in zf.namelist()
            
            # Load and verify
            import json
            with zf.open("org.json") as f:
                data = json.load(f)
            assert data["mdm_url"] == "https://mdm.example.com"