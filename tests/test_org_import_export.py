"""Tests for OrganizationManager import functionality."""
import json
import plistlib
import tempfile
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509

from apple_device_cli.orgs.manager import OrganizationManager, Organization


def create_test_cert_and_key():
    """Create a test certificate and key for PKCS12 testing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    subject = issuer = x509.Name([
        x509.NameAttribute(x509.oid.NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(x509.oid.NameOID.ORGANIZATION_NAME, "Test Import Org"),
        x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "Apple Configurator: Test Import Org"),
    ])
    
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=365))
        .sign(private_key, hashes.SHA256())
    )
    
    return cert, private_key


def create_pkcs12_blob(cert: x509.Certificate, private_key, password: str = "password") -> bytes:
    """Create a PKCS12 blob encrypted with the given password."""
    from cryptography.hazmat.primitives.serialization import pkcs12, BestAvailableEncryption
    
    return pkcs12.serialize_key_and_certificates(
        name=b"identity",
        key=private_key,
        cert=cert,
        cas=None,
        encryption_algorithm=BestAvailableEncryption(password.encode())
    )


def create_org_metadata(name="Test Org", org_id="com.test", mdm_url="https://mdm.example.com"):
    """Create standard org metadata dict."""
    return {
        "name": name,
        "org_id": org_id,
        "address": "123 Test St",
        "phone": "555-1234",
        "email": "test@example.com",
        "mdm_url": mdm_url,
        "checkin_url": "https://mdm.example.com/checkin",
        "mdm_topic": "com.test.topic",
        "identity_ref": None,
        "mdm_description": "Test MDM",
        "created_at": "2024-01-01T00:00:00",
    }


@pytest.fixture
def temp_orgs_dir():
    """Create a temporary directory for org storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def manager(temp_orgs_dir):
    """Create an OrganizationManager with temp directory."""
    return OrganizationManager(temp_orgs_dir)


class TestImportFromDir:
    """Tests for _import_from_dir method."""

    def test_import_from_dir_success(self, manager, temp_orgs_dir):
        """Should import org from directory with org.json."""
        # Create source directory with org.json
        src_dir = temp_orgs_dir / "source"
        src_dir.mkdir()
        
        metadata = create_org_metadata()
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        # Import
        org = manager._import_from_dir(src_dir)
        
        assert org.name == "Test Org"
        assert org.org_id == "com.test"
        assert org.mdm_url == "https://mdm.example.com"

    def test_import_from_dir_missing_org_json(self, manager, temp_orgs_dir):
        """Should raise ValueError when org.json is missing."""
        src_dir = temp_orgs_dir / "source"
        src_dir.mkdir()
        
        with pytest.raises(ValueError, match="Missing org.json"):
            manager._import_from_dir(src_dir)

    def test_import_from_dir_missing_name(self, manager, temp_orgs_dir):
        """Should raise ValueError when name is missing in org.json."""
        src_dir = temp_orgs_dir / "source"
        src_dir.mkdir()
        
        metadata = {"org_id": "com.test"}  # No name
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        with pytest.raises(ValueError, match="Org name not found"):
            manager._import_from_dir(src_dir)

    def test_import_from_dir_overwrite_existing(self, manager, temp_orgs_dir):
        """Should overwrite existing org when overwrite=True."""
        # Create initial org
        org1 = Organization(name="Test Org", org_id="com.test.v1")
        manager.save_org(org1)
        
        # Create source with updated metadata
        src_dir = temp_orgs_dir / "source"
        src_dir.mkdir()
        metadata = create_org_metadata(org_id="com.test.v2")
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        # Import with overwrite
        org = manager._import_from_dir(src_dir, overwrite=True)
        
        assert org.org_id == "com.test.v2"
        # Verify old data is gone
        loaded = manager.get_org("Test Org")
        assert loaded.org_id == "com.test.v2"

    def test_import_from_dir_no_overwrite(self, manager, temp_orgs_dir):
        """Should raise ValueError when org exists and overwrite=False."""
        # Create initial org
        org1 = Organization(name="Test Org", org_id="com.test.v1")
        manager.save_org(org1)
        
        # Create source with different org_id
        src_dir = temp_orgs_dir / "source"
        src_dir.mkdir()
        metadata = create_org_metadata(org_id="com.test.v2")
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        with pytest.raises(ValueError, match="already exists"):
            manager._import_from_dir(src_dir, overwrite=False)


class TestImportFromOrganization:
    """Tests for _import_from_organization method (Apple Configurator format)."""

    def test_import_from_organization_success(self, manager, temp_orgs_dir):
        """Should import .organization file (PKCS12 format)."""
        cert, private_key = create_test_cert_and_key()
        
        # Create PKCS12 blob with password encryption
        import base64
        pkcs12_data = create_pkcs12_blob(cert, private_key, "password")
        
        # Create plist with identity reference
        org_plist = {
            "name": "Test Configurator Org",
            "UUID": "com.configurator.org",
            "address": "456 Configurator Ave",
            "phone": "555-CONFIG",
            "email": "config@example.com",
            "mdmServer": "https://config-mdm.example.com",
            "identityReference": base64.b64encode(pkcs12_data).decode()
        }
        
        # Create .organization file
        org_file = temp_orgs_dir / "test.organization"
        org_file.write_bytes(plistlib.dumps(org_plist))
        
        # Import
        org = manager._import_from_organization(org_file, "password")
        
        assert org.name == "Test Configurator Org"
        assert org.org_id == "com.configurator.org"
        assert org.mdm_url == "https://config-mdm.example.com"
        assert org.cert_path is not None
        assert org.key_path is not None

    def test_import_from_organization_wrong_password(self, manager, temp_orgs_dir):
        """Should raise ValueError with wrong PKCS12 password."""
        import base64
        cert, private_key = create_test_cert_and_key()
        
        # Create PKCS12 blob with correct password
        pkcs12_data = create_pkcs12_blob(cert, private_key, "correct_password")
        
        org_plist = {
            "name": "Test Org",
            "identityReference": base64.b64encode(pkcs12_data).decode()
        }
        
        org_file = temp_orgs_dir / "test.organization"
        org_file.write_bytes(plistlib.dumps(org_plist))
        
        # Try to import with wrong password
        with pytest.raises(ValueError, match="Failed to decode identity"):
            manager._import_from_organization(org_file, "wrong_password")

    def test_import_from_organization_missing_identity_reference(self, manager, temp_orgs_dir):
        """Should raise ValueError when identityReference is missing."""
        org_plist = {
            "name": "Test Org",
            # No identityReference
        }
        
        org_file = temp_orgs_dir / "test.organization"
        org_file.write_bytes(plistlib.dumps(org_plist))
        
        with pytest.raises(ValueError, match="identityReference not found"):
            manager._import_from_organization(org_file, "password")

    def test_import_from_organization_missing_name(self, manager, temp_orgs_dir):
        """Should raise ValueError when name is missing."""
        import base64
        cert, private_key = create_test_cert_and_key()
        
        # Create PKCS12 blob with password
        pkcs12_data = create_pkcs12_blob(cert, private_key, "password")
        
        org_plist = {
            "identityReference": base64.b64encode(pkcs12_data).decode()
            # No name
        }
        
        org_file = temp_orgs_dir / "test.organization"
        org_file.write_bytes(plistlib.dumps(org_plist))
        
        with pytest.raises(ValueError, match="Organization name not found"):
            manager._import_from_organization(org_file, "password")


class TestImportOrg:
    """Tests for import_org public method."""

    def test_import_org_from_directory(self, manager, temp_orgs_dir):
        """Should import org from directory path."""
        src_dir = temp_orgs_dir / "import_source"
        src_dir.mkdir()
        metadata = create_org_metadata()
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        org = manager.import_org(src_dir)
        
        assert org.name == "Test Org"
        assert manager.get_org("Test Org") is not None

    def test_import_org_from_directory_path_string(self, manager, temp_orgs_dir):
        """Should accept directory path as string."""
        src_dir = temp_orgs_dir / "import_source"
        src_dir.mkdir()
        metadata = create_org_metadata()
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        org = manager.import_org(str(src_dir))
        
        assert org.name == "Test Org"

    def test_import_org_from_zip(self, manager, temp_orgs_dir):
        """Should import org from .zip file."""
        # Create source directory
        src_dir = temp_orgs_dir / "zip_source"
        src_dir.mkdir()
        metadata = create_org_metadata(name="Zip Org")
        (src_dir / "org.json").write_text(json.dumps(metadata))
        
        # Create zip file
        zip_path = temp_orgs_dir / "import.zip"
        shutil_make_zip = __import__('shutil').make_archive
        shutil_make_zip(str(zip_path.with_suffix("")), "zip", src_dir)
        
        org = manager.import_org(zip_path)
        
        assert org.name == "Zip Org"

    def test_import_org_from_organization_file(self, manager, temp_orgs_dir):
        """Should import org from .organization file."""
        import base64
        cert, private_key = create_test_cert_and_key()
        
        # Create PKCS12 blob with password
        pkcs12_data = create_pkcs12_blob(cert, private_key, "password")
        
        org_plist = {
            "name": "File Org",
            "UUID": "com.file.org",
            "identityReference": base64.b64encode(pkcs12_data).decode()
        }
        
        org_file = temp_orgs_dir / "test.organization"
        org_file.write_bytes(plistlib.dumps(org_plist))
        
        org = manager.import_org(org_file)
        
        assert org.name == "File Org"

    def test_import_org_invalid_path(self, manager):
        """Should raise ValueError for invalid path."""
        with pytest.raises(ValueError, match="Invalid path"):
            manager.import_org("/nonexistent/invalid/path.file")


class TestExportOrg:
    """Tests for export_org method."""

    def test_export_org_to_directory(self, manager, temp_orgs_dir):
        """Should export org to directory."""
        # Create org first
        org = Organization(name="Export Test", org_id="com.export.test")
        manager.save_org(org)
        
        # Export to temp directory
        dest = temp_orgs_dir / "exported"
        result = manager.export_org("Export Test", dest)
        
        assert result is True
        assert (dest / "Export Test" / "org.json").exists()

    def test_export_org_to_zip(self, manager, temp_orgs_dir):
        """Should export org to .zip file."""
        # Create org first
        org = Organization(name="Zip Export", org_id="com.zip.export")
        manager.save_org(org)
        
        # Export to zip
        zip_path = temp_orgs_dir / "exported.zip"
        result = manager.export_org("Zip Export", zip_path)
        
        assert result is True
        assert zip_path.exists()

    def test_export_org_nonexistent(self, manager, temp_orgs_dir):
        """Should return False for nonexistent org."""
        result = manager.export_org("Nonexistent Org", temp_orgs_dir / "export")
        
        assert result is False


class TestDeleteOrg:
    """Tests for delete_org method (additional edge cases)."""

    def test_delete_org_with_files(self, manager, temp_orgs_dir):
        """Should delete org directory with all files."""
        org = Organization(name="Delete Test", org_id="com.delete.test")
        manager.save_org(org)
        
        # Verify directory exists
        org_dir = manager.orgs_dir / "Delete_Test"
        assert org_dir.exists()
        
        # Delete
        result = manager.delete_org("Delete Test")
        
        assert result is True
        assert not org_dir.exists()

    def test_delete_org_multiple_times(self, manager, temp_orgs_dir):
        """Second delete should return False."""
        org = Organization(name="Delete Test", org_id="com.delete.test")
        manager.save_org(org)
        
        manager.delete_org("Delete Test")
        result = manager.delete_org("Delete Test")
        
        assert result is False
