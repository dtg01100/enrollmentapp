import pytest
import tempfile
import plistlib
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch, MagicMock
from apple_device_cli.orgs.manager import OrganizationManager


# Sample MDM payload for mocking - minimal valid structure
SAMPLE_MDM_PAYLOAD = {
    'PayloadContent': [{
        'PayloadType': 'com.apple.mdm',
        'PayloadIdentifier': 'com.apple.mgmt.external.test',
        'PayloadUUID': 'TEST-UUID-1234-5678',
        'CheckInURL': 'https://test.example.com/checkin',
        'ServerURL': 'https://test.example.com/mdm',
        'Topic': 'com.apple.mgmt.TestTopic',
        'Description': 'Test MDM Provider',
        'IdentityCertificateUUID': 'test-cert-uuid',
    }],
    'PayloadDisplayName': 'Test MDM Profile',
    'PayloadIdentifier': 'com.test.mobileconfig',
    'PayloadOrganization': 'Test Org',
    'PayloadDescription': 'Test MDM Provider',  # This is what the code looks for
    'PayloadUUID': 'TEST-CONFIG-UUID',
    'PayloadVersion': 1,
}


@pytest.fixture
def mock_mobileconfig():
    """Create a mock mobileconfig file in temp directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        mobileconfig_path = tmp / "test.mobileconfig"
        mobileconfig_path.write_bytes(b"mock signed mobileconfig data")
        yield mobileconfig_path, tmp


def make_mock_subprocess(plist_data):
    """Create a mock subprocess.run that returns the plist as stdout."""
    def mock_run(cmd, capture_output=False, **kwargs):
        result = MagicMock(spec=CompletedProcess)
        result.returncode = 0
        result.stdout = plistlib.dumps(plist_data)
        result.stderr = b''
        return result
    return mock_run


@pytest.mark.parametrize("field,expected", [
    ("name", "Test Org"),
    ("mdm_url", "https://test.example.com/mdm"),
    ("checkin_url", "https://test.example.com/checkin"),
    ("mdm_topic", "com.apple.mgmt.TestTopic"),
    ("identity_ref", "test-cert-uuid"),
    ("mdm_description", "Test MDM Provider"),  # From PayloadDescription
    ("org_id", "com.apple.mgmt.TestTopic"),
])
def test_import_mobileconfig_extracts_mdm_fields(mock_mobileconfig, field, expected):
    """Test that import_mobileconfig correctly extracts MDM fields from a profile."""
    mobileconfig_path, _ = mock_mobileconfig
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    
    with patch('subprocess.run', side_effect=make_mock_subprocess(SAMPLE_MDM_PAYLOAD)):
        with patch('apple_device_cli.orgs.manager.pkcs7.load_der_pkcs7_certificates', return_value=[]):
            org = mgr.import_mobileconfig(mobileconfig_path)
    
    assert getattr(org, field) == expected, f"Field '{field}' mismatch"


def test_import_mobileconfig_raises_on_duplicate(mock_mobileconfig):
    """Test that importing the same org twice raises ValueError."""
    mobileconfig_path, _ = mock_mobileconfig
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    
    with patch('subprocess.run', side_effect=make_mock_subprocess(SAMPLE_MDM_PAYLOAD)):
        with patch('apple_device_cli.orgs.manager.pkcs7.load_der_pkcs7_certificates', return_value=[]):
            mgr.import_mobileconfig(mobileconfig_path)
            
            with pytest.raises(ValueError, match="already exists"):
                mgr.import_mobileconfig(mobileconfig_path)


def test_import_mobileconfig_raises_on_nonexistent_file():
    """Test that importing a nonexistent file raises ValueError."""
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    
    with pytest.raises(ValueError, match="File not found"):
        mgr.import_mobileconfig('/nonexistent/path.mobileconfig')


def test_import_mobileconfig_saves_org(mock_mobileconfig):
    """Test that import_mobileconfig persists the org correctly."""
    mobileconfig_path, _ = mock_mobileconfig
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    
    with patch('subprocess.run', side_effect=make_mock_subprocess(SAMPLE_MDM_PAYLOAD)):
        with patch('apple_device_cli.orgs.manager.pkcs7.load_der_pkcs7_certificates', return_value=[]):
            org = mgr.import_mobileconfig(mobileconfig_path)
            retrieved = mgr.get_org(org.name)
    
    assert retrieved is not None
    assert retrieved.name == org.name
    assert retrieved.mdm_url == org.mdm_url


def test_import_mobileconfig_raises_on_missing_payload_organization(mock_mobileconfig):
    """Test that missing PayloadOrganization raises ValueError."""
    mobileconfig_path, _ = mock_mobileconfig
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    bad_payload = {
        'PayloadContent': SAMPLE_MDM_PAYLOAD['PayloadContent'],
        'PayloadDisplayName': 'Test',
        'PayloadIdentifier': 'com.test',
        'PayloadOrganization': '',  # Empty string instead of None
        'PayloadUUID': 'TEST',
        'PayloadVersion': 1,
    }
    
    with patch('subprocess.run', side_effect=make_mock_subprocess(bad_payload)):
        with pytest.raises(ValueError, match="Missing PayloadOrganization"):
            mgr.import_mobileconfig(mobileconfig_path)


def test_import_mobileconfig_acquires_lock(mock_mobileconfig):
    """import_mobileconfig must hold fcntl.flock during filesystem operations."""
    import fcntl

    mobileconfig_path, tmp = mock_mobileconfig
    mgr = OrganizationManager(tmp)

    with patch('subprocess.run', side_effect=make_mock_subprocess(SAMPLE_MDM_PAYLOAD)), \
         patch('apple_device_cli.orgs.manager.pkcs7.load_der_pkcs7_certificates', return_value=[]), \
         patch('apple_device_cli.orgs.manager.fcntl.flock') as mock_flock, \
         patch('apple_device_cli.orgs.manager.os.open', return_value=42) as mock_open, \
         patch('apple_device_cli.orgs.manager.os.close') as mock_close:
        mgr.import_mobileconfig(mobileconfig_path)

    # os.open must be called with O_CREAT on a per-org lock path
    assert mock_open.called
    lock_path_arg = mock_open.call_args[0][0]
    assert str(lock_path_arg).endswith(".Test_Org.lock")
    assert lock_path_arg.name.startswith(".")

    # flock must be called with LOCK_EX first (acquire) and LOCK_UN last (release)
    flock_calls = mock_flock.call_args_list
    assert len(flock_calls) >= 2
    assert flock_calls[0].args[1] == fcntl.LOCK_EX
    assert any(c.args[1] == fcntl.LOCK_UN for c in flock_calls)

    # fd must be closed
    mock_close.assert_called_once_with(42)


def test_import_mobileconfig_raises_on_openssl_failure(mock_mobileconfig):
    """Test that failed openssl verification raises ValueError."""
    mobileconfig_path, _ = mock_mobileconfig
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    
    def mock_run_fail(cmd, capture_output=False, **kwargs):
        result = MagicMock(spec=CompletedProcess)
        result.returncode = 1
        result.stdout = b''
        result.stderr = b'verification failed'
        return result
    
    with patch('subprocess.run', side_effect=mock_run_fail):
        with pytest.raises(ValueError, match="Failed to parse mobileconfig"):
            mgr.import_mobileconfig(mobileconfig_path)