# Import SimpleMDM Mobileconfig to Organization

**Date:** 2026-04-23
**Status:** Approved

## Overview

Add `OrganizationManager.import_mobileconfig(path)` to create an Organization from an MDM `.mobileconfig` profile downloaded from SimpleMDM (or similar MDM provider). This is a read/import operation only — it does not modify or re-sign the profile.

## File Format

MDM `.mobileconfig` files from SimpleMDM are DER-encoded PKCS7 signed data (not raw bplist). The plist payload is signed inside the PKCS7 envelope.

## Extraction Method

Use `openssl smime -verify -noverify -inform DER` to extract the embedded plist payload. This avoids a hardcoded byte offset approach.

```python
result = subprocess.run(
    ['openssl', 'smime', '-verify', '-inform', 'DER', '-noverify', '-in', str(path)],
    capture_output=True
)
payload = plistlib.loads(result.stdout)
```

## Fields Extracted from Mobileconfig Plist

| Plist Key | Organization Field | Notes |
|-----------|-------------------|-------|
| `PayloadOrganization` | `name` | Required. Used as org name. |
| `ServerURL` | `mdm_url` | MDM server URL |
| `CheckInURL` | — | Stored as `checkin_url` field on Organization |
| `Topic` | `org_id` | MDM topic/CA identifier |
| `IdentityCertificateUUID` | — | Stored as `identity_ref` field (note only) |
| `PayloadDescription` | — | Stored as `mdm_description` field |

## Validation

- If `PayloadOrganization` is missing or empty → raise `ValueError("Missing organization name in mobileconfig")`
- If an org with the same `name` already exists in `orgs_dir` → raise `ValueError(f"Organization '{name}' already exists")`

## New Organization Fields

Add two new optional fields to the `Organization` dataclass:

```python
@dataclass
class Organization:
    name: str
    org_id: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    mdm_url: str | None = None       # NEW: MDM ServerURL
    checkin_url: str | None = None   # NEW: SCEP CheckInURL
    mdm_topic: str | None = None      # NEW: MDM Topic
    identity_ref: str | None = None  # NEW: IdentityCertificateUUID
    mdm_description: str | None = None # NEW: PayloadDescription
    cert_path: str | None = None
    key_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
```

## `to_dict` / `from_dict` Updates

Add new fields to `to_dict()` output and parse them in `from_dict()`.

## Error Handling

- If `openssl` is not available → raise `RuntimeError("openssl not found — required to parse signed mobileconfig")`
- If the file is not valid DER PKCS7 → `openssl` returns non-zero exit code and stderr, propagate as `ValueError`
- If plist parsing fails → raise `ValueError("Failed to parse mobileconfig plist")`

## API

```python
class OrganizationManager:
    def import_mobileconfig(self, path: str | Path) -> Organization:
        """Import org from MDM .mobileconfig file (PKCS7-signed DER)."""
        ...
```

## Testing

- Add unit test: import valid SimpleMDM mobileconfig → org has correct MDM fields
- Add unit test: import when org already exists → raises `ValueError`
- Add unit test: import file with missing PayloadOrganization → raises `ValueError`
- The existing `Capital Candy Company Inc.organization` test data does not have MDM fields, so a new `.mobileconfig` fixture file is needed for testing

## Out of Scope

- Re-signing or modifying the mobileconfig
- Apple Configurator `.organization` import/export
- Certificate/key handling (identity cert from MDM is separate from enrollment identity)
