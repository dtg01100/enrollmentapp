#!/usr/bin/env python3
"""
iOS Supervision Enrollment Tool for Linux
Based on reverse engineering of Apple Configurator's cfgutil protocol.

Key URLs discovered:
- https://albert.apple.com/deviceservices/deviceActivation
- https://albert.apple.com/deviceservices/drmHandshake
- https://purplerestore.apple.com/index/v5_all_builds.plist

Requires: libimobiledevice (usbmuxd), pyusb, construct

Usage:
    ./enroll.py list
    ./enroll.py gui         # Launch graphical interface
    ./enroll.py org list
    ./enroll.py org import --path ./myorg
    ./enroll.py org export --name "My Org" --path ./exported_org
    ./enroll.py org create --name "My Org" --org-id "com.example"
"""

import os
import sys
import plistlib
import argparse
import struct
import json
import shutil
import subprocess
import base64
import warnings
from pathlib import Path
from datetime import datetime

SUPERVISION_WELL_KNOWN_SERVICES = [
    "com.apple.mobile.lockdown",
    "com.apple.mobile.lockdown.activation_state",
    "com.apple.springboardservices",
    "com.apple.configurator.mdk.notification-service",
]

DEFAULT_ORGS_DIR = Path.home() / ".config" / "enrollment" / "orgs"


class MobileDeviceConnection:
    def __init__(self, udid=None):
        self.udid = udid
        self.connection = None

    def connect(self, service="com.apple.mobile.lockdown"):
        pass

    def disconnect(self):
        pass

    def send_plist(self, plist_dict):
        pass

    def recv_plist(self):
        pass


class PlistProtocol:
    """Helper for plist encoding/decoding used by lockdown protocol."""

    @staticmethod
    def encode(plist_dict):
        """Encode dict to plist bytes."""
        return plistlib.dumps(plist_dict)

    @staticmethod
    def decode(data):
        """Decode plist bytes to dict."""
        return plistlib.loads(data)


class Organization:
    """Represents a supervising organization."""

    def __init__(
        self,
        name,
        org_id=None,
        address=None,
        phone=None,
        email=None,
        cert_path=None,
        key_path=None,
        created_at=None,
    ):
        self.name = name
        self.org_id = org_id
        self.address = address
        self.phone = phone
        self.email = email
        self.cert_path = cert_path
        self.key_path = key_path
        self.created_at = created_at or datetime.now().isoformat()

    def to_dict(self):
        return {
            "name": self.name,
            "org_id": self.org_id,
            "address": self.address,
            "phone": self.phone,
            "email": self.email,
            "cert_path": str(self.cert_path) if self.cert_path else None,
            "key_path": str(self.key_path) if self.key_path else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data["name"],
            org_id=data.get("org_id"),
            address=data.get("address"),
            phone=data.get("phone"),
            email=data.get("email"),
            cert_path=data.get("cert_path"),
            key_path=data.get("key_path"),
            created_at=data.get("created_at"),
        )

    def save(self, org_dir):
        """Save org to directory."""
        org_dir = Path(org_dir)
        org_dir.mkdir(parents=True, exist_ok=True)

        metadata = self.to_dict()
        del metadata["cert_path"]
        del metadata["key_path"]

        with open(org_dir / "org.json", "w") as f:
            json.dump(metadata, f, indent=2)

        if self.cert_path and Path(self.cert_path).exists():
            shutil.copy(self.cert_path, org_dir / "cert.der")
        if self.key_path and Path(self.key_path).exists():
            shutil.copy(self.key_path, org_dir / "key.der")

    @classmethod
    def load(cls, org_dir):
        """Load org from directory."""
        org_dir = Path(org_dir)
        with open(org_dir / "org.json", "r") as f:
            data = json.load(f)

        data["cert_path"] = (
            str(org_dir / "cert.der") if (org_dir / "cert.der").exists() else None
        )
        data["key_path"] = (
            str(org_dir / "key.der") if (org_dir / "key.der").exists() else None
        )

        return cls.from_dict(data)


class OrganizationManager:
    """Manages supervising organizations."""

    def __init__(self, orgs_dir=None):
        self.orgs_dir = Path(orgs_dir) if orgs_dir else DEFAULT_ORGS_DIR
        self.orgs_dir.mkdir(parents=True, exist_ok=True)

    def list_orgs(self):
        """List all stored organizations."""
        orgs = []
        for item in self.orgs_dir.iterdir():
            if item.is_dir() and (item / "org.json").exists():
                try:
                    org = Organization.load(item)
                    orgs.append(org.to_dict())
                except Exception:
                    pass
        return orgs

    def get_org(self, name):
        """Get org by name."""
        org_dir = self.orgs_dir / self._sanitize_name(name)
        if not org_dir.exists():
            return None
        return Organization.load(org_dir)

    def save_org(self, org):
        """Save an organization."""
        org_dir = self.orgs_dir / self._sanitize_name(org.name)
        org.save(org_dir)

    def delete_org(self, name):
        """Delete an organization."""
        org_dir = self.orgs_dir / self._sanitize_name(name)
        if org_dir.exists():
            shutil.rmtree(org_dir)
            return True
        return False

    def import_org(self, path, password="password"):
        """Import org from directory, .zip file, or Apple Configurator .organization file."""
        path = Path(path)

        if path.suffix == ".organization":
            return self._import_from_organization(path, password)
        elif path.is_file() and path.suffix == ".zip":
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                shutil.unpack_archive(path, tmpdir)
                return self._import_from_dir(Path(tmpdir))
        elif path.is_dir():
            return self._import_from_dir(path)
        else:
            raise ValueError(f"Invalid path: {path}")

    def _import_from_organization(self, org_file, password):
        """Import org from Apple Configurator .organization file."""
        import warnings
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.hazmat.backends import default_backend

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
                private_key, certificate, additional_certs = (
                    pkcs12.load_key_and_certificates(
                        identity_data, password.encode(), backend=default_backend()
                    )
                )
        except Exception as e:
            raise ValueError(f"Failed to decode identity (wrong password?): {e}")

        dest_dir = self.orgs_dir / self._sanitize_name(name)
        if dest_dir.exists():
            print(f"Warning: Overwriting existing org '{name}'")

        dest_dir.mkdir(parents=True, exist_ok=True)

        from cryptography.hazmat.primitives import serialization

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
                    "created_at": datetime.now().isoformat(),
                },
                f,
                indent=2,
            )

        with open(dest_dir / "cert.der", "wb") as f:
            f.write(cert_der)

        with open(dest_dir / "key.der", "wb") as f:
            f.write(key_der)

        org = Organization(
            name=name,
            org_id=data.get("UUID"),
            address=data.get("address"),
            phone=data.get("phone"),
            email=data.get("email"),
            cert_path=str(dest_dir / "cert.der"),
            key_path=str(dest_dir / "key.der"),
        )
        return org

    def _import_from_dir(self, src_dir):
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
            print(f"Warning: Overwriting existing org '{name}'")

        org.save(dest_dir)
        return org

    def export_org(self, name, dest_path):
        """Export org to directory or .zip file."""
        org = self.get_org(name)
        if not org:
            print(f"Error: Organization '{name}' not found")
            return False

        dest_path = Path(dest_path)
        org_dir = self.orgs_dir / self._sanitize_name(name)

        if dest_path.suffix == ".zip":
            shutil.make_archive(str(dest_path.with_suffix("")), "zip", org_dir)
        else:
            dest_path.mkdir(parents=True, exist_ok=True)
            shutil.copytree(org_dir, dest_path / org.name, dirs_exist_ok=True)
        return True

    def _sanitize_name(self, name):
        """Sanitize org name for use as directory name."""
        return "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)


class IOSEnrollmentTool:
    def __init__(
        self,
        cert_path=None,
        key_path=None,
        org_name=None,
        org_id=None,
        org_address=None,
        org_phone=None,
        org_email=None,
    ):
        self.cert_path = cert_path
        self.key_path = key_path
        self.org_name = org_name
        self.org_id = org_id
        self.org_address = org_address
        self.org_phone = org_phone
        self.org_email = org_email
        self._supervision_cert = None
        self._supervision_key = None
        self.org_manager = OrganizationManager()

    def load_supervision_identity(self):
        """Load supervising certificate and private key from DER-encoded files."""
        self._supervision_cert = None
        self._supervision_key = None
        if self.cert_path:
            with open(self.cert_path, "rb") as f:
                self._supervision_cert = f.read()
        if self.key_path:
            with open(self.key_path, "rb") as f:
                self._supervision_key = f.read()
        return self._supervision_cert, self._supervision_key

    def list_devices(self):
        """List connected iOS devices via usbmuxd.

        Uses libimobiledevice's idevicepair and ideviceinfo to enumerate
        connected iOS devices through usbmuxd.
        """
        devices = []
        try:
            result = subprocess.run(
                ["idevicepair", "list"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    udid = line.strip()
                    if udid and len(udid) > 10:
                        device_info = self.get_device_info(udid)
                        if device_info and device_info.get("deviceName") != "Error":
                            devices.append(device_info)
        except Exception as e:
            print(f"Error listing devices: {e}", file=sys.stderr)
        return devices

    def pair_device(self, udid=None):
        """Pair with a device using lockdown protocol.

        Sends pair request to com.apple.mobile.lockdown service.
        """
        pass

    def prepare_device(
        self, udid=None, skip_panes=None, ipsw_path=None, build=None, variant=None
    ):
        """
        Prepare device for supervision enrollment.

        Similar to 'cfgutil prepare' command. This command:
        1. Connects to the device in recovery mode if needed
        2. Applies skip-xxx options to Setup Assistant
        3. Triggers restore with appropriate IPSW

        Args:
            udid: Device UDID (or 'all' for all devices)
            skip_panes: List of panes to skip in Setup Assistant
            ipsw_path: Optional path to specific IPSW
            build: Specific iOS build version
            variant: Build variant (e.g., 'Customer')
        """
        skip_panes = skip_panes or []

    def make_supervised(
        self, udid=None, forbid_itunes_pairing=False, forbid_mac_pairing=False
    ):
        """
        Make device supervised using certificate.

        This requires:
        - Supervising organization certificate (-C)
        - Private key (-K)
        - Organization name (--org-name)

        Creates supervision identity and applies to device.
        """
        cert, key = self.load_supervision_identity()
        if not cert or not key:
            print(
                "Error: Both certificate (-C) and private key (-K) are required for supervision"
            )
            return False

        if not self.org_name:
            print("Error: Organization name (--org-name) is required for supervision")
            return False

    def activate_device(self, udid=None):
        """
        Activate a paired device.

        Uses albert.apple.com/deviceActivation endpoint.
        Requires supervision identity for supervised devices.
        """
        pass

    def restore_device(
        self, udid=None, ipsw_path=None, skip_panes=None, build=None, variant=None
    ):
        """
        Restore IPSW on device using idevicerestore.

        Args:
            udid: Device UDID
            ipsw_path: Path to IPSW file
            skip_panes: List of Setup Assistant panes to skip
            build: Specific build number
            variant: Build variant
        """
        if not udid:
            print("Error: --udid is required for restore")
            return False
        if not ipsw_path:
            print("Error: --ipsw path is required for restore")
            return False

        ipsw_path = Path(ipsw_path)
        if not ipsw_path.exists():
            print(f"Error: IPSW file not found: {ipsw_path}")
            return False

        print(f"Restoring device {udid} with {ipsw_path}...")

        # Try to use idevicerestore
        brew_prefix = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True
        ).stdout.strip()
        idevicerestore_path = (
            Path(brew_prefix) / "bin" / "idevicerestore" if brew_prefix else None
        )

        if idevicerestore_path and idevicerestore_path.exists():
            env = os.environ.copy()
            env["PATH"] = f"{brew_prefix}/bin:{env.get('PATH', '')}"
            env["LD_LIBRARY_PATH"] = (
                f"{brew_prefix}/lib:{brew_prefix}/lib64:{env.get('LD_LIBRARY_PATH', '')}"
            )

            result = subprocess.run(
                [str(idevicerestore_path), "-u", udid, str(ipsw_path)],
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
            if result.returncode == 0:
                print("Restore completed successfully")
                return True
            else:
                print(f"Restore failed: {result.stderr}")
                return False
        else:
            print("Error: idevicerestore not found. Install with:")
            print("  brew install local/enrollment/idevicerestore")
            print("  (or run ./homebrew/install.sh)")
            return False

    def erase_device(self, udid=None, skip_esim=False):
        """
        Erase device (wipe all content and settings) using idevicerestore.

        Args:
            udid: Device UDID
            skip_esim: If True, skip erasing embedded eSIMs
        """
        if not udid:
            print("Error: --udid is required for erase")
            return False
        print(f"Erasing device {udid}...")
        if skip_esim:
            print("  (keeping eSIM data)")

        brew_prefix = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True
        ).stdout.strip()
        idevicerestore_path = (
            Path(brew_prefix) / "bin" / "idevicerestore" if brew_prefix else None
        )

        if idevicerestore_path and idevicerestore_path.exists():
            env = os.environ.copy()
            env["PATH"] = f"{brew_prefix}/bin:{env.get('PATH', '')}"
            env["LD_LIBRARY_PATH"] = (
                f"{brew_prefix}/lib:{brew_prefix}/lib64:{env.get('LD_LIBRARY_PATH', '')}"
            )

            cmd = [str(idevicerestore_path), "-u", udid]
            if skip_esim:
                cmd.append("--no-eic")
            cmd.extend(["-E"])  # Erase all and restore

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, env=env
            )
            if result.returncode == 0:
                print("Erase completed successfully")
                return True
            else:
                print(f"Erase failed: {result.stderr}")
                return False
        else:
            # Fallback: just put in recovery mode
            result = subprocess.run(
                ["ideviceenterrecovery", udid],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                print(f"Device {udid} entered recovery mode (ready for restore)")
                print("Note: For full erase, install idevicerestore:")
                print("  brew install local/enrollment/idevicerestore")
                return True
            else:
                print(f"Could not enter recovery mode: {result.stderr}")
                return False

    def update_device(
        self, udid=None, skip_software_update=False, skip_update_completed=False
    ):
        """
        Update device to latest available iOS version.

        Args:
            udid: Device UDID
            skip_software_update: Skip software update pane in Setup Assistant
            skip_update_completed: Skip update completed pane
        """
        if not udid:
            print("Error: --udid is required for update")
            return False

        # Get current version
        info = self.get_device_info(udid)
        current = info.get("firmwareVersion", "Unknown")
        current_build = info.get("buildVersion", "Unknown")

        print(f"Updating device {udid} to latest iOS...")
        print(f"  Current version: iOS {current} ({current_build})")
        print("  Fetching latest available build...")

        # Fetch latest builds from Apple
        try:
            import urllib.request
            import plistlib

            url = "https://purplerestore.apple.com/index/v5_all_builds.plist"
            response = urllib.request.urlopen(url, timeout=30)
            data = plistlib.loads(response.read())

            # Find latest build for this device
            # Simplified - actual implementation would match device model
            print("  Note: Full update requires idevicerestore (not installed)")
            print("  For now, use Settings > General > Software Update on device")
            return True
        except Exception as e:
            print(f"  Could not fetch updates: {e}")
            print("  For now, use Settings > General > Software Update on device")
            return False

    def get_device_info(self, udid=None):
        """
        Get device properties using libimobiledevice.

        Returns dict with:
        - UDID
        - deviceName
        - deviceType
        - buildVersion
        - firmwareVersion
        """
        if not udid:
            return {}
        try:
            result = subprocess.run(
                ["ideviceinfo", "-u", udid], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                info = {}
                for line in result.stdout.strip().split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        info[key.strip()] = value.strip()
                return {
                    "UDID": info.get("UniqueDeviceID", udid),
                    "deviceName": info.get("DeviceName", "Unknown"),
                    "deviceType": info.get("ProductType", "Unknown"),
                    "buildVersion": info.get("BuildVersion", "Unknown"),
                    "firmwareVersion": info.get("ProductVersion", "Unknown"),
                    "model": info.get("ModelNumber", ""),
                    "serialNumber": info.get("SerialNumber", ""),
                }
        except Exception as e:
            print(f"Error getting device info: {e}", file=sys.stderr)
        return {
            "UDID": udid,
            "deviceName": "Error",
            "deviceType": "",
            "buildVersion": "",
            "firmwareVersion": "",
        }

    def enroll_supervised(self, udid=None):
        """
        Complete supervision enrollment workflow.

        This combines:
        1. prepare_device()
        2. make_supervised()
        3. activate_device()
        """
        pass


def cmd_org_list(args):
    """List all stored organizations."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)
    orgs = manager.list_orgs()

    if not orgs:
        print("No organizations stored.")
        print(f"  Org storage: {manager.orgs_dir}")
        return

    if args.json:
        print(json.dumps(orgs, indent=2))
    else:
        print(f"Organizations stored in: {manager.orgs_dir}")
        print()
        for org in orgs:
            print(f"  {org['name']}")
            if org.get("org_id"):
                print(f"    ID: {org['org_id']}")
            print(f"    Created: {org.get('created_at', 'unknown')}")


def cmd_org_import(args):
    """Import organization from path."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)

    try:
        org = manager.import_org(args.path)
        print(f"Imported organization: {org.name}")
        print(f"  Cert: {'Yes' if org.cert_path else 'No'}")
        print(f"  Key: {'Yes' if org.key_path else 'No'}")
    except Exception as e:
        print(f"Error importing organization: {e}")
        sys.exit(1)


def cmd_org_export(args):
    """Export organization to path."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)

    dest = Path(args.path)
    fmt = "zip" if dest.suffix == ".zip" else "dir"

    if manager.export_org(args.name, args.path):
        print(f"Exported '{args.name}' to {args.path}")
    else:
        print(f"Failed to export organization '{args.name}'")
        sys.exit(1)


def cmd_org_delete(args):
    """Delete an organization."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)

    if manager.delete_org(args.name):
        print(f"Deleted organization: {args.name}")
    else:
        print(f"Organization not found: {args.name}")
        sys.exit(1)


def cmd_org_create(args):
    """Create a new organization (metadata only, cert/key must be added separately)."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)

    org = Organization(
        name=args.name,
        org_id=args.org_id,
        address=args.address,
        phone=args.phone,
        email=args.email,
    )

    if args.cert:
        org.cert_path = str(Path(args.cert).resolve())
    if args.key:
        org.key_path = str(Path(args.key).resolve())

    manager.save_org(org)
    print(f"Created organization: {org.name}")
    if args.cert:
        print(f"  Certificate: {org.cert_path}")
    if args.key:
        print(f"  Private key: {org.key_path}")


def cmd_org_set_cert(args):
    """Set certificate for an organization."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)

    org = manager.get_org(args.name)
    if not org:
        print(f"Organization not found: {args.name}")
        sys.exit(1)

    org.cert_path = str(Path(args.cert).resolve())
    manager.save_org(org)
    print(f"Set certificate for '{args.name}'")


def cmd_org_set_key(args):
    """Set private key for an organization."""
    manager = OrganizationManager(Path(args.orgs_dir) if args.orgs_dir else None)

    org = manager.get_org(args.name)
    if not org:
        print(f"Organization not found: {args.name}")
        sys.exit(1)

    org.key_path = str(Path(args.key).resolve())
    manager.save_org(org)
    print(f"Set private key for '{args.name}'")


def main():
    parser = argparse.ArgumentParser(
        description="iOS Supervision Enrollment Tool for Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./enroll.py list
  ./enroll.py org list
  ./enroll.py org create --name "My Org" --org-id "com.example" -C cert.der -K key.der
  ./enroll.py org import --path ./my_org.zip
  ./enroll.py org export --name "My Org" --path ./exported_org.zip
  ./enroll.py prepare --udid all --org-name "My Org" -C cert.der -K key.der
        """,
    )
    parser.add_argument(
        "--orgs-dir",
        help=f"Custom organizations directory (default: {DEFAULT_ORGS_DIR})",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    list_parser = subparsers.add_parser("list", help="List connected devices")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    gui_parser = subparsers.add_parser("gui", help="Launch graphical interface")

    pair_parser = subparsers.add_parser("pair", help="Pair with device")
    pair_parser.add_argument("--udid", help="Device UDID (omit for first available)")

    prepare_parser = subparsers.add_parser(
        "prepare", help="Prepare device for enrollment"
    )
    prepare_parser.add_argument(
        "--udid", default="all", help="Device UDID (default: all)"
    )
    prepare_parser.add_argument("--ipsw", help="Path to specific IPSW file")
    prepare_parser.add_argument("--build", help="Specific iOS build (e.g., 21A329)")
    prepare_parser.add_argument("--variant", help="Build variant (e.g., Customer)")
    prepare_parser.add_argument("--skip-restore-completed", action="store_true")
    prepare_parser.add_argument("--skip-update-completed", action="store_true")
    prepare_parser.add_argument("--skip-all", action="store_true")
    prepare_parser.add_argument("--skip-passcode", action="store_true")
    prepare_parser.add_argument("--skip-language", action="store_true")
    prepare_parser.add_argument("--skip-region", action="store_true")

    make_parser = subparsers.add_parser(
        "make-supervised", help="Make device supervised"
    )
    make_parser.add_argument("--udid", required=True, help="Device UDID")
    make_parser.add_argument("--forbid-itunes-pairing", action="store_true")
    make_parser.add_argument("--forbid-mac-pairing", action="store_true")

    activate_parser = subparsers.add_parser("activate", help="Activate device")
    activate_parser.add_argument("--udid", required=True, help="Device UDID")

    restore_parser = subparsers.add_parser("restore", help="Restore device with IPSW")
    restore_parser.add_argument("--udid", required=True, help="Device UDID")
    restore_parser.add_argument("--ipsw", required=True, help="Path to IPSW file")
    restore_parser.add_argument("--skip-restore-completed", action="store_true")
    restore_parser.add_argument("--skip-update-completed", action="store_true")

    erase_parser = subparsers.add_parser("erase", help="Erase device (wipe)")
    erase_parser.add_argument("--udid", required=True, help="Device UDID")
    erase_parser.add_argument(
        "--skip-esim", action="store_true", help="Skip erasing eSIMs"
    )

    update_parser = subparsers.add_parser("update", help="Update device to latest iOS")
    update_parser.add_argument("--udid", required=True, help="Device UDID")
    update_parser.add_argument(
        "--skip-software-update", action="store_true", help="Skip software update pane"
    )
    update_parser.add_argument(
        "--skip-update-completed",
        action="store_true",
        help="Skip update completed pane",
    )

    info_parser = subparsers.add_parser("info", help="Get device info")
    info_parser.add_argument("--udid", help="Device UDID (omit for first available)")
    info_parser.add_argument("--json", action="store_true", help="Output as JSON")

    enroll_parser = subparsers.add_parser("enroll", help="Full enrollment workflow")
    enroll_parser.add_argument("--udid", required=True, help="Device UDID")
    enroll_parser.add_argument("--ipsw", help="Optional specific IPSW")

    org_parser = subparsers.add_parser("org", help="Organization management")
    org_subparsers = org_parser.add_subparsers(dest="org_command", help="Org commands")

    org_list_parser = org_subparsers.add_parser("list", help="List organizations")
    org_list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    org_import_parser = org_subparsers.add_parser(
        "import", help="Import organization from directory or zip"
    )
    org_import_parser.add_argument("--path", required=True, help="Path to import")

    org_export_parser = org_subparsers.add_parser(
        "export", help="Export organization to directory or zip"
    )
    org_export_parser.add_argument("--name", required=True, help="Organization name")
    org_export_parser.add_argument("--path", required=True, help="Export destination")

    org_delete_parser = org_subparsers.add_parser(
        "delete", help="Delete an organization"
    )
    org_delete_parser.add_argument("--name", required=True, help="Organization name")

    org_create_parser = org_subparsers.add_parser(
        "create", help="Create a new organization"
    )
    org_create_parser.add_argument("--name", required=True, help="Organization name")
    org_create_parser.add_argument("--org-id", help="Organization unique ID")
    org_create_parser.add_argument("--address", help="Organization address")
    org_create_parser.add_argument("--phone", help="Organization phone")
    org_create_parser.add_argument("--email", help="Organization email")
    org_create_parser.add_argument("-C", "--cert", help="Path to certificate (DER)")
    org_create_parser.add_argument("-K", "--key", help="Path to private key (DER)")

    org_set_cert_parser = org_subparsers.add_parser(
        "set-cert", help="Set certificate for an org"
    )
    org_set_cert_parser.add_argument("--name", required=True, help="Organization name")
    org_set_cert_parser.add_argument(
        "-C", "--cert", required=True, help="Path to certificate (DER)"
    )

    org_set_key_parser = org_subparsers.add_parser(
        "set-key", help="Set private key for an org"
    )
    org_set_key_parser.add_argument("--name", required=True, help="Organization name")
    org_set_key_parser.add_argument(
        "-K", "--key", required=True, help="Path to private key (DER)"
    )

    parser.add_argument(
        "-C", "--cert", help="Path to DER-encoded supervising certificate"
    )
    parser.add_argument("-K", "--key", help="Path to DER-encoded private key")
    parser.add_argument("--org-name", help="Supervising organization name")
    parser.add_argument("--org-id", help="Supervising organization unique ID")
    parser.add_argument("--org-address", help="Supervising organization address")
    parser.add_argument("--org-phone", help="Supervising organization phone")
    parser.add_argument("--org-email", help="Supervising organization email")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.command == "org":
        if args.org_command == "list":
            cmd_org_list(args)
        elif args.org_command == "import":
            cmd_org_import(args)
        elif args.org_command == "export":
            cmd_org_export(args)
        elif args.org_command == "delete":
            cmd_org_delete(args)
        elif args.org_command == "create":
            cmd_org_create(args)
        elif args.org_command == "set-cert":
            cmd_org_set_cert(args)
        elif args.org_command == "set-key":
            cmd_org_set_key(args)
        else:
            org_parser.print_help()
        return

    if args.command == "gui":
        gui_path = Path(__file__).parent / "enroll_gui.py"
        if gui_path.exists():
            subprocess.Popen([sys.executable, str(gui_path)])
        else:
            print("Error: enroll_gui.py not found")
            sys.exit(1)
        return

    tool = IOSEnrollmentTool(
        cert_path=args.cert,
        key_path=args.key,
        org_name=args.org_name,
        org_id=args.org_id,
        org_address=getattr(args, "org_address", None),
        org_phone=getattr(args, "org_phone", None),
        org_email=getattr(args, "org_email", None),
    )

    if args.verbose:
        print(f"Command: {args.command}", file=sys.stderr)

    if args.command == "list":
        devices = tool.list_devices()
        if args.json:
            print(json.dumps(devices, indent=2))
        else:
            for d in devices:
                print(f"{d.get('UDID', 'unknown')}\t{d.get('deviceName', 'Unknown')}")

    elif args.command == "pair":
        tool.pair_device(args.udid)
    elif args.command == "prepare":
        skip_panes = []
        if args.skip_all:
            skip_panes.append("all")
        if args.skip_restore_completed:
            skip_panes.append("restore-completed")
        if args.skip_update_completed:
            skip_panes.append("update-completed")
        if args.skip_passcode:
            skip_panes.append("passcode")
        if args.skip_language:
            skip_panes.append("language")
        if args.skip_region:
            skip_panes.append("region")
        tool.prepare_device(args.udid, skip_panes, args.ipsw, args.build, args.variant)
    elif args.command == "make-supervised":
        tool.make_supervised(
            args.udid, args.forbid_itunes_pairing, args.forbid_mac_pairing
        )
    elif args.command == "activate":
        tool.activate_device(args.udid)
    elif args.command == "restore":
        tool.restore_device(
            args.udid, args.ipsw, skip_panes=args.skip_restore_completed
        )
    elif args.command == "erase":
        tool.erase_device(args.udid, skip_esim=args.skip_esim)
    elif args.command == "update":
        tool.update_device(
            args.udid,
            skip_software_update=args.skip_software_update,
            skip_update_completed=args.skip_update_completed,
        )
    elif args.command == "info":
        info = tool.get_device_info(args.udid)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            for k, v in info.items():
                print(f"{k}: {v}")
    elif args.command == "enroll":
        tool.enroll_supervised(args.udid)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
