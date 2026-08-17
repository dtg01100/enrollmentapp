"""Verify the ios-enroll supervised enrollment flow against a real iOS 26 device.

Runs the app's own ``make_supervised`` path (connect -> activate -> supervise
via SetCloudConfiguration -> WiFi -> MDM InstallProfileSilent) with the org's
on-disk cert/key/mobileconfig files, and prints every request/response plist
exchanged on ``com.apple.mobile.MCInstall`` and ``com.apple.mobileactivationd``.

Usage::

    .venv/bin/python scripts/verify_ios26_enroll.py <UDID> [ORG_NAME]

Use on a scratch/test device. Supervision is persistent until the device is
erased or re-enrolled.
"""
from __future__ import annotations

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# 1. Intercept MCInstall (com.apple.mobile.MCInstall) plist exchanges.
# ---------------------------------------------------------------------------
import pymobiledevice3.services.mobile_config as mobile_config_mod

_orig_mc_send_recv = mobile_config_mod.MobileConfigService._send_recv


async def _logged_mc_send_recv(self, request, *args, **kwargs):
    print("\n[MCInstall] >>> REQUEST plist:")
    print(json.dumps(request, indent=2, default=str), flush=True)
    try:
        response = await _orig_mc_send_recv(self, request, *args, **kwargs)
    except Exception as exc:
        print(f"[MCInstall] <<< EXCEPTION {type(exc).__name__}: {exc}", flush=True)
        raise
    print("[MCInstall] <<< RESPONSE plist:")
    print(json.dumps(response, indent=2, default=str), flush=True)
    return response


mobile_config_mod.MobileConfigService._send_recv = _logged_mc_send_recv

# ---------------------------------------------------------------------------
# 2. Intercept activation daemon (com.apple.mobileactivationd) plist exchanges.
# ---------------------------------------------------------------------------
import pymobiledevice3.lockdown as lockdown_mod

_orig_start_lockdown_service = lockdown_mod.LockdownClient.start_lockdown_service
_TAG_BY_SERVICE = {"com.apple.mobileactivationd": "Activation"}


async def _logged_start_lockdown_service(self, name, *args, **kwargs):
    svc = await _orig_start_lockdown_service(self, name, *args, **kwargs)
    tag = _TAG_BY_SERVICE.get(name)
    if tag is not None:
        orig_send_recv_plist = svc.send_recv_plist

        async def logged_send_recv_plist(data, *a, **kw):
            print(f"\n[{tag}] >>> REQUEST plist:")
            print(json.dumps(data, indent=2, default=str), flush=True)
            try:
                resp = await orig_send_recv_plist(data, *a, **kw)
            except Exception as exc:
                print(f"[{tag}] <<< EXCEPTION {type(exc).__name__}: {exc}", flush=True)
                raise
            print(f"[{tag}] <<< RESPONSE plist:")
            print(json.dumps(resp, indent=2, default=str), flush=True)
            return resp

        svc.send_recv_plist = logged_send_recv_plist
    return svc


lockdown_mod.LockdownClient.start_lockdown_service = _logged_start_lockdown_service

# ---------------------------------------------------------------------------
# 3. Run the real enrollment flow.
# ---------------------------------------------------------------------------
from apple_device_cli.enrollment.supervised import make_supervised  # noqa: E402
from apple_device_cli.orgs.manager import OrganizationManager  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify ios-enroll supervised enrollment against a real device "
        "(prints all MCInstall and activation plist exchanges)."
    )
    parser.add_argument("udid", help="Device UDID to enroll (see `ios-enroll device list`)")
    parser.add_argument(
        "--org",
        default="Capital Candy Company",
        help="Organization name to use (default: %(default)s)",
    )
    return parser.parse_args()


def progress(msg: str) -> None:
    print(f"  [progress] {msg}", flush=True)


def main() -> int:
    args = parse_args()

    manager = OrganizationManager()
    org = manager.get_org(args.org)
    if org is None:
        print(f"organization not found: {args.org}", file=sys.stderr)
        return 1
    if not (org.cert_path and org.key_path):
        print(f"org '{org.name}' missing cert/key", file=sys.stderr)
        return 1

    print(f"=== org '{org.name}'")
    print(f"    mdm_url:              {org.mdm_url}")
    print(f"    checkin_url:          {org.checkin_url}")
    print(f"    wifi_config_path:     {org.wifi_config_path}")
    print(f"    mdm_mobileconfig_path:{org.mdm_mobileconfig_path}")
    print(f"=== running make_supervised on UDID {args.udid}")
    print("    this will: activate -> SetCloudConfiguration (supervise) -> WiFi -> MDM InstallProfileSilent\n")

    result = make_supervised(
        cert_path=org.cert_path,
        key_path=org.key_path,
        org_name=org.name,
        org_uuid=org.org_id,
        skip_list=["passcode"],
        mdm_url=org.mdm_url,
        mdm_checkin_url=org.checkin_url,
        mdm_topic=org.mdm_topic,
        wifi_config=org.wifi_config_path,
        mdm_mobileconfig=org.mdm_mobileconfig_path,
        udid=args.udid,
        progress_callback=progress,
    )

    print("\n=== RESULT ===")
    print(result)
    print(
        f"success={result.success} supervised={result.supervised} "
        f"mdm_enrolled={result.mdm_enrolled} wifi_installed={result.wifi_installed}"
    )
    if result.errors:
        print("errors:")
        for err in result.errors:
            print(f"  - {err}")
    return 0 if result.success else 2


if __name__ == "__main__":
    sys.exit(main())
