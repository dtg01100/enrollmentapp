import base64
import json
import plistlib
import shutil
import subprocess
import tempfile
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12, pkcs7
from cryptography.hazmat.backends import default_backend

DEFAULT_ORGS_DIR = Path.home() / ".config" / "apple_device_cli" / "orgs"


@dataclass
class Organization:
    name: str
    org_id: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    mdm_url: str | None = None        # MDM ServerURL
    checkin_url: str | None = None   # SCEP CheckInURL
    mdm_topic: str | None = None     # MDM Topic
    identity_ref: str | None = None  # IdentityCertificateUUID
    mdm_description: str | None = None # PayloadDescription
    cert_path: str | None = None
    key_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

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
            created_at=data.get("created_at") or datetime.now().isoformat(),
        )

    def save(self, org_dir: Path, skip_copy: bool = False):
        org_dir.mkdir(parents=True, exist_ok=True)
        metadata = self.to_dict()
        del metadata["cert_path"]
        del metadata["key_path"]
        with open(org_dir / "org.json", "w") as f:
            json.dump(metadata, f, indent=2)
        if not skip_copy:
            if self.cert_path and Path(self.cert_path).exists():
                dest_cert = org_dir / "cert.der"
                if Path(self.cert_path) != dest_cert:
                    shutil.copy(self.cert_path, dest_cert)
            if self.key_path and Path(self.key_path).exists():
                dest_key = org_dir / "key.der"
                if Path(self.key_path) != dest_key:
                    shutil.copy(self.key_path, dest_key)

    @classmethod
    def load(cls, org_dir: Path) -> "Organization":
        with open(org_dir / "org.json", "r") as f:
            data = json.load(f)
        data["cert_path"] = str(org_dir / "cert.der") if (org_dir / "cert.der").exists() else None
        data["key_path"] = str(org_dir / "key.der") if (org_dir / "key.der").exists() else None
        return cls.from_dict(data)


class OrganizationManager:
    def __init__(self, orgs_dir: Path | None = None):
        self.orgs_dir = orgs_dir or DEFAULT_ORGS_DIR
        self.orgs_dir.mkdir(parents=True, exist_ok=True)

    def list_orgs(self) -> list[Organization]:
        import logging
        orgs = []
        failed = 0
        for item in self.orgs_dir.iterdir():
            if item.is_dir() and (item / "org.json").exists():
                try:
                    orgs.append(Organization.load(item))
                except Exception as e:
                    failed += 1
                    logging.warning(f"Failed to load org from {item}: {e}")
        if failed:
            logging.warning(f"Failed to load {failed} organization(s)")
        return orgs

    def get_org(self, name: str) -> Organization | None:
        org_dir = self.orgs_dir / self._sanitize_name(name)
        if not org_dir.exists():
            return None
        return Organization.load(org_dir)

    def save_org(self, org: Organization, overwrite: bool = False):
        org_dir = self.orgs_dir / self._sanitize_name(org.name)
        if not overwrite and org_dir.exists():
            raise ValueError(f"Organization '{org.name}' already exists")
        org.save(org_dir)

    def delete_org(self, name: str) -> bool:
        org_dir = self.orgs_dir / self._sanitize_name(name)
        if org_dir.exists():
            shutil.rmtree(org_dir)
            return True
        return False

    def _sanitize_name(self, name: str) -> str:
        return "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)

    def import_org(self, path: str | Path, password: str = "password") -> Organization:
        """Import org from directory, .zip file, or Apple Configurator .organization file."""
        path = Path(path)

        if path.suffix == ".organization":
            return self._import_from_organization(path, password)
        elif path.is_file() and path.suffix == ".zip":
            with tempfile.TemporaryDirectory() as tmpdir:
                shutil.unpack_archive(path, tmpdir)
                return self._import_from_dir(Path(tmpdir), overwrite=True)
        elif path.is_dir():
            return self._import_from_dir(path, overwrite=True)
        else:
            raise ValueError(f"Invalid path: {path}")

    def _import_from_organization(self, org_file: Path, password: str) -> Organization:
        """Import org from Apple Configurator .organization file (PKCS12)."""
        with open(org_file, "rb") as f:
            content = f.read()

        try:
            data = plistlib.loads(content)
        except Exception as e:
            raise ValueError(f"Failed to parse organization file: {e}")

        name = data.get("name")
        if not name:
            raise ValueError("Organization name not found")

        identity_ref = data.get("identityReference")
        if not identity_ref:
            raise ValueError("identityReference not found in organization file")

        if isinstance(identity_ref, str):
            identity_data = base64.b64decode(identity_ref)
        else:
            identity_data = identity_ref

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                private_key, certificate, _ = pkcs12.load_key_and_certificates(
                    identity_data, password.encode(), backend=default_backend()
                )
        except Exception as e:
            raise ValueError(f"Failed to decode identity (wrong password?): {e}") from None

        if certificate is None or private_key is None:
            raise ValueError("PKCS12 blob missing certificate or private key")

        dest_dir = self.orgs_dir / self._sanitize_name(name)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        cert_der = certificate.public_bytes(serialization.Encoding.DER)
        key_der = private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

        with open(dest_dir / "org.json", "w") as f:
            json.dump(
                {
                    "name": name,
                    "org_id": data.get("UUID"),
                    "address": data.get("address"),
                    "phone": data.get("phone"),
                    "email": data.get("email"),
                    "mdm_url": data.get("mdmServer"),
                    "created_at": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        with open(dest_dir / "cert.der", "wb") as f:
            f.write(cert_der)

        with open(dest_dir / "key.der", "wb") as f:
            f.write(key_der)

        return Organization(
            name=name,
            org_id=data.get("UUID"),
            address=data.get("address"),
            phone=data.get("phone"),
            email=data.get("email"),
            mdm_url=data.get("mdmServer"),
            cert_path=str(dest_dir / "cert.der"),
            key_path=str(dest_dir / "key.der"),
        )

    def _import_from_dir(self, src_dir: Path, overwrite: bool = False) -> Organization:
        """Import org from directory."""
        if not (src_dir / "org.json").exists():
            raise ValueError("Missing org.json in import directory")

        with open(src_dir / "org.json", "r") as f:
            data = json.load(f)

        name = data.get("name")
        if not name:
            raise ValueError("Org name not found in metadata")

        org = Organization.from_dict(data)

        dest_dir = self.orgs_dir / self._sanitize_name(name)
        if dest_dir.exists():
            if not overwrite:
                raise ValueError(f"Organization '{name}' already exists")
            shutil.rmtree(dest_dir)

        org.save(org_dir=dest_dir, skip_copy=False)
        return org

    def import_mobileconfig(self, path: str | Path) -> Organization:
        """Import org from MDM .mobileconfig file (PKCS7-signed DER).

        Extracts MDM server metadata and any client certificates from the PKCS7 signature.
        """
        path = Path(path)
        if not path.exists():
            raise ValueError(f"File not found: {path}")

        with open(path, "rb") as f:
            data = f.read()

        # Extract certificates from PKCS7 structure
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                pkcs7_certs = pkcs7.load_der_pkcs7_certificates(data)
        except Exception:
            pkcs7_certs = []

        # Parse the plist content (verified via PKCS7 signature)
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

        existing_org = self.get_org(name)
        if existing_org:
            raise ValueError(f"Organization '{name}' already exists")

        mdm_url = None
        checkin_url = None
        mdm_topic = None
        identity_ref = None
        for item in payload.get('PayloadContent', []):
            if isinstance(item, dict) and item.get('PayloadType') == 'com.apple.mdm':
                mdm_url = item.get('ServerURL')
                checkin_url = item.get('CheckInURL')
                mdm_topic = item.get('Topic')
                identity_ref = item.get('IdentityCertificateUUID')
                break

        dest_dir = self.orgs_dir / self._sanitize_name(name)
        dest_dir.mkdir(parents=True, exist_ok=True)

        if pkcs7_certs:
            cert_der = pkcs7_certs[0].public_bytes(serialization.Encoding.DER)
            with open(dest_dir / "cert.der", "wb") as f:
                f.write(cert_der)

        org = Organization(
            name=name,
            org_id=mdm_topic,
            mdm_url=mdm_url,
            checkin_url=checkin_url,
            mdm_topic=mdm_topic,
            identity_ref=identity_ref,
            mdm_description=payload.get('PayloadDescription'),
            cert_path=str(dest_dir / "cert.der") if pkcs7_certs else None,
        )

        org.save(org_dir=dest_dir, skip_copy=True)
        return org

    def export_org(self, name: str, dest_path: str | Path) -> bool:
        """Export org to directory or .zip file."""
        org = self.get_org(name)
        if not org:
            return False

        dest_path = Path(dest_path)
        org_dir = self.orgs_dir / self._sanitize_name(name)

        if dest_path.suffix == ".zip":
            shutil.make_archive(str(dest_path.with_suffix("")), "zip", org_dir)
        else:
            dest_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(org_dir, dest_path / org.name, dirs_exist_ok=True)
        return True