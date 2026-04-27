#!/usr/bin/env python3
"""Migrate organizations from old enrollment storage to OrganizationManager."""
import shutil
from pathlib import Path

from apple_device_cli.orgs.manager import OrganizationManager, Organization

OLD_ORGS_DIR = Path.home() / ".config" / "enrollment" / "orgs"
NEW_ORGS_DIR = Path.home() / ".config" / "apple_device_cli" / "orgs"

def migrate():
    if not OLD_ORGS_DIR.exists():
        print("No old orgs dir found, nothing to migrate")
        return

    manager = OrganizationManager(NEW_ORGS_DIR)
    migrated = 0
    skipped = 0

    for item in OLD_ORGS_DIR.iterdir():
        if not item.is_dir() or not (item / "org.json").exists():
            continue

        existing = manager.get_org(item.name)
        if existing is not None:
            print(f"  Skip {item.name}: already exists in new location")
            skipped += 1
            continue

        try:
            org = Organization.load(item)
            manager.save_org(org)
            print(f"  Migrated: {org.name}")
            migrated += 1
        except Exception as e:
            print(f"  Failed {item.name}: {e}")

    print(f"\nMigrated {migrated}, skipped {skipped}")

if __name__ == "__main__":
    migrate()