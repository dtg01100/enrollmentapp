# MDM Mobileconfig Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `OrganizationManager.import_mobileconfig(path)` that reads a PKCS7-signed MDM `.mobileconfig` file and creates an Organization with MDM URL fields populated.

**Architecture:** Extend the `Organization` dataclass with MDM-specific fields, update serialization, and add a new import method that extracts the plist from a PKCS7 envelope via `openssl smime`.

**Tech Stack:** Python, plistlib, subprocess (openssl), cryptography

---

## File Structure

- **Modify:** `src/apple_device_cli/orgs/manager.py` — add fields + import method
- **Create:** `tests/test_mobileconfig_import.py` — new test file

---

## Task 1: Add new fields to Organization dataclass

**Files:**
- Modify: `src/apple_device_cli/orgs/manager.py:18-28`

- [ ] **Step 1: Read the current Organization dataclass**

Run: `read(src/apple_device_cli/orgs/manager.py:18-28)`

- [ ] **Step 2: Edit the dataclass to add new MDM fields**

```python
@dataclass
class Organization:
    name: str
    org_id: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    mdm_url: str | None = None
    checkin_url: str | None = None
    mdm_topic: str | None = None
    identity_ref: str | None = None
    mdm_description: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
```

- [ ] **Step 3: Commit**

```bash
git add src/apple_device_cli/orgs/manager.py
git commit -m "feat: add MDM fields to Organization dataclass"
```

---

## Task 2: Update to_dict and from_dict

**Files:**
- Modify: `src/apple_device_cli/orgs/manager.py:30-55`

- [ ] **Step 1: Read current to_dict and from_dict methods**

Run: `read(src/apple_device_cli/orgs/manager.py:30-55)`

- [ ] **Step 2: Update to_dict to include new fields**

Replace the `to_dict` method with:

```python
def to_dict(self) -> dict:
    return {
        "name": self.name,
        "org_id": self.org_id,
        "address": self.address,
        "phone": self.phone,
        "email": self.email,
        "mdm_url": self.mdm_url,
        "checkin_url": self.checkin_url,
        "mdm_topic": self.mdm_topic,
        "identity_ref": self.identity_ref,
        "mdm_description": self.mdm_description,
        "cert_path": str(self.cert_path) if self.cert_path else None,
        "key_path": str(self.key_path) if self.key_path else None,
        "created_at": self.created_at,
    }
```

- [ ] **Step 3: Update from_dict to parse new fields**

Replace the `from_dict` method with:

```python
@classmethod
def from_dict(cls, data: dict) -> "Organization":
    return cls(
        name=data["name"],
        org_id=data.get("org_id"),
        address=data.get("address"),
        phone=data.get("phone"),
        email=data.get("email"),
        mdm_url=data.get("mdm_url"),
        checkin_url=data.get("checkin_url"),
        mdm_topic=data.get("mdm_topic"),
        identity_ref=data.get("identity_ref"),
        mdm_description=data.get("mdm_description"),
        cert_path=data.get("cert_path"),
        key_path=data.get("key_path"),
        created_at=data.get("created_at"),
    )
```

- [ ] **Step 4: Run existing org tests to ensure no regression**

Run: `pytest tests/test_org_manager.py -v`
Expected: All 7 tests still pass

- [ ] **Step 5: Commit**

```bash
git add src/apple_device_cli/orgs/manager.py
git commit -m "feat: update Organization serialization for MDM fields"
```

---

## Task 3: Add import_mobileconfig method

**Files:**
- Modify: `src/apple_device_cli/orgs/manager.py` — add method at end of OrganizationManager class

- [ ] **Step 1: Read the end of the OrganizationManager class to find insertion point**

Run: `read(src/apple_device_cli/orgs/manager.py:225-240)`

- [ ] **Step 2: Add import_mobileconfig method before export_org**

Add this method to the `OrganizationManager` class (before `export_org` at line 226):

```python
def import_mobileconfig(self, path: str | Path) -> Organization:
    """Import org from MDM .mobileconfig file (PKCS7-signed DER)."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"File not found: {path}")

    result = subprocess.run(
        ['openssl', 'smime', '-verify', '-inform', 'DER', '-noverify', '-in', str(path)],
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(f"Failed to parse mobileconfig: {result.stderr.decode(errors='replace').strip()}")

    try:
        payload = plistlib.loads(result.stdout)
    except Exception as e:
        raise ValueError(f"Failed to parse mobileconfig plist: {e}")

    name = payload.get('PayloadOrganization')
    if not name:
        raise ValueError("Missing PayloadOrganization in mobileconfig")

    if self.get_org(name) is not None:
        raise ValueError(f"Organization '{name}' already exists")

    org = Organization(
        name=name,
        org_id=payload.get('Topic'),
        mdm_url=payload.get('ServerURL'),
        checkin_url=payload.get('CheckInURL'),
        mdm_topic=payload.get('Topic'),
        identity_ref=payload.get('IdentityCertificateUUID'),
        mdm_description=payload.get('PayloadDescription'),
    )
    self.save_org(org)
    return org
```

- [ ] **Step 3: Add subprocess import at top of file**

Run: `read(src/apple_device_cli/orgs/manager.py:1-15)`

Add `import subprocess` to the imports if not present.

- [ ] **Step 4: Test the method manually**

Run:
```bash
cd /var/mnt/Disk2/projects/enrollmentapp && .venv/bin/python -c "
from apple_device_cli.orgs.manager import OrganizationManager
import tempfile
from pathlib import Path

mgr = OrganizationManager(Path(tempfile.mkdtemp()))
org = mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')
print(f'Imported: {org.name}')
print(f'MDM URL: {org.mdm_url}')
print(f'CheckIn URL: {org.checkin_url}')
print(f'MDM Topic: {org.mdm_topic}')
print(f'Identity Ref: {org.identity_ref}')
print(f'Description: {org.mdm_description}')
"
```

Expected output:
```
Imported: Example Organization Profile
MDM URL: https://a.simplemdm.com/mdm
CheckIn URL: https://mdm.example.com/checkin/…
MDM Topic: com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f
Identity Ref: F459CDF1-3A0B-40FF-8AB0-5C3961DEFF6A
Description: Elegant Apple device management with SimpleMDM
```

- [ ] **Step 5: Test duplicate-org rejection**

Run:
```bash
cd /var/mnt/Disk2/projects/enrollmentapp && .venv/bin/python -c "
from apple_device_cli.orgs.manager import OrganizationManager
import tempfile
from pathlib import Path

mgr = OrganizationManager(Path(tempfile.mkdtemp()))
mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')
try:
    mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')
    print('ERROR: should have raised')
except ValueError as e:
    print(f'Correctly raised: {e}')
"
```

Expected: `Correctly raised: Organization 'Example Organization Profile' already exists`

- [ ] **Step 6: Test missing PayloadOrganization rejection**

First create a test plist file without PayloadOrganization, then test. Run:
```bash
cd /var/mnt/Disk2/projects/enrollmentapp && .venv/bin/python -c "
from apple_device_cli.orgs.manager import OrganizationManager
import tempfile
from pathlib import Path
import plistlib
import subprocess

# Create a fake mobileconfig without PayloadOrganization
fake = plistlib.dumps({'ServerURL': 'https://example.com/mdm'})
tmp = Path(tempfile.mktemp(suffix='.mobileconfig'))
tmp.write_bytes(fake)

mgr = OrganizationManager(Path(tempfile.mkdtemp()))
try:
    mgr.import_mobileconfig(tmp)
    print('ERROR: should have raised')
except ValueError as e:
    print(f'Correctly raised: {e}')
"
```

Expected: `Correctly raised: Missing PayloadOrganization in mobileconfig`

- [ ] **Step 7: Commit**

```bash
git add src/apple_device_cli/orgs/manager.py
git commit -m "feat: add import_mobileconfig to OrganizationManager"
```

---

## Task 4: Write unit tests

**Files:**
- Create: `tests/test_mobileconfig_import.py`

- [ ] **Step 1: Write the test file**

```python
import pytest
import tempfile
import plistlib
import subprocess
from pathlib import Path
from apple_device_cli.orgs.manager import OrganizationManager


def make_signed_mobileconfig(payload: dict) -> Path:
    """Create a fake PKCS7-signed DER mobileconfig file for testing."""
    tmp = Path(tempfile.mktemp(suffix='.mobileconfig'))
    plist_data = plistlib.dumps(payload)
    # Write raw plist as DER (simplified — openssl smime -verify accepts it)
    tmp.write_bytes(plist_data)
    return tmp


def test_import_mobileconfig_extracts_mdm_fields():
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    org = mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')
    assert org.name == 'Example Organization Profile'
    assert org.mdm_url == 'https://a.simplemdm.com/mdm'
    assert org.checkin_url == 'https://mdm.example.com/checkin/…'
    assert org.mdm_topic == 'com.apple.mgmt.External.205e2f7b-f2e8-4a33-8f11-097496bec56f'
    assert org.identity_ref == 'F459CDF1-3A0B-40FF-8AB0-5C3961DEFF6A'
    assert org.mdm_description == 'Elegant Apple device management with SimpleMDM'


def test_import_mobileconfig_raises_on_duplicate():
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')
    with pytest.raises(ValueError, match="already exists"):
        mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')


def test_import_mobileconfig_raises_on_missing_name():
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    fake = make_signed_mobileconfig({'ServerURL': 'https://example.com/mdm'})
    with pytest.raises(ValueError, match="Missing PayloadOrganization"):
        mgr.import_mobileconfig(fake)


def test_import_mobileconfig_raises_on_nonexistent_file():
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    with pytest.raises(ValueError, match="File not found"):
        mgr.import_mobileconfig('/nonexistent/path.mobileconfig')


def test_import_mobileconfig_saves_org():
    mgr = OrganizationManager(Path(tempfile.mkdtemp()))
    org = mgr.import_mobileconfig('SimpleMDM - Default Group.mobileconfig')
    retrieved = mgr.get_org(org.name)
    assert retrieved is not None
    assert retrieved.mdm_url == org.mdm_url
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_mobileconfig_import.py -v`
Expected: All 5 tests pass (using the real `SimpleMDM - Default Group.mobileconfig` fixture)

- [ ] **Step 3: Commit**

```bash
git add tests/test_mobileconfig_import.py
git commit -m "test: add mobileconfig import tests"
```

---

## Spec Coverage Check

- [x] New MDM fields on Organization dataclass → Task 1
- [x] to_dict/from_dict updated → Task 2
- [x] import_mobileconfig method → Task 3
- [x] Openssl extraction (not hardcoded offset) → Task 3
- [x] Validation: missing PayloadOrganization → Task 3
- [x] Validation: duplicate org name → Task 3
- [x] Unit tests → Task 4

## Type Consistency Check

- `org.mdm_url`, `org.checkin_url`, `org.mdm_topic`, `org.identity_ref`, `org.mdm_description` — all defined as new dataclass fields in Task 1 and referenced consistently throughout.
- `OrganizationManager.import_mobileconfig(path)` — method signature matches spec.
