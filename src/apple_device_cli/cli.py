from __future__ import annotations

import sys
from pathlib import Path
import shutil
from typing import Callable
import json

import asyncio
import typer


from apple_device_cli import __version__
from apple_device_cli.core.redaction import (
    redact_address,
    redact_email,
    redact_identifier,
    redact_name,
    redact_org_identifier,
    redact_path,
    redact_phone,
    redact_url,
    sanitize_text,
)
from apple_device_cli.device.connection import (
    ensure_device_pairing,
    get_device_info,
    list_devices,
)
from apple_device_cli.device.info import DeviceInfo

from apple_device_cli.cli_actions import (
    OrgNotFoundError,
    OrgAlreadyExistsError,
    create_org,
    delete_org,
    generate_org,
    import_mobileconfig,
    import_org,
    set_org_field,
    set_org_wifi,
)
from apple_device_cli.orgs.manager import OrganizationManager, Organization
from apple_device_cli.orgs.identity import generate_org_identity, load_cert_info
from apple_device_cli.enrollment.skip_panes import resolve_skip_panes
from apple_device_cli.enrollment.supervised import make_supervised
from apple_device_cli.enrollment.activation import activate_device
from apple_device_cli.core.exceptions import AppleDeviceError
from apple_device_cli.restore.cache import cache_state, resolve_cache_dir
from apple_device_cli.restore.engine import (
    ProgressEvent,
    get_product_type_for_udid,
    list_signed_versions,
    restore_device,
)
from apple_device_cli.restore.errors import RestoreEngineError


def _normalize_prompted_path(path: str | None) -> str | None:
    """Normalize a path entered interactively.

    Users sometimes paste paths wrapped in shell quotes; strip those and
    normalize surrounding whitespace so file existence checks behave as expected.
    """
    if path is None:
        return None

    normalized = path.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1].strip()

    return normalized or None


def _display_name(value: str | None) -> str:
    return redact_name(value)


def _set_org_field(
    name: str,
    field_name: str,
    value: str,
    label: str,
) -> None:
    """Thin presentation wrapper around ``cli_actions.set_org_field``.

    All business logic lives in ``cli_actions.set_org_field``; this wrapper
    only handles the user-facing success/error display.
    """
    manager = OrganizationManager()
    try:
        set_org_field(manager, name, field_name, value, label)
    except OrgNotFoundError as e:
        typer.secho(f"Organization not found: {_display_name(name)}", fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.secho(f"Set {label} for '{_display_name(name)}'", fg=typer.colors.GREEN)


def _display_udid(value: str | None) -> str:
    return redact_identifier(value, prefix=6, suffix=4)


def _display_org_id(value: str | None) -> str:
    return redact_org_identifier(value)

app = typer.Typer(
    help="iOS device supervised enrollment CLI",
)
device_app = typer.Typer(help="Device management commands")
org_app = typer.Typer(help="Organization management commands")
enroll_app = typer.Typer(help="Enrollment commands")


app.add_typer(device_app, name="device", invoke_without_command=True)
app.add_typer(org_app, name="org", invoke_without_command=True)
app.add_typer(enroll_app, name="enroll", invoke_without_command=True)


def _device_help() -> None:
    """Help message for incomplete device commands."""
    typer.secho("ios-enroll device - Device management commands\n", fg=typer.colors.BLUE, bold=True)
    typer.echo("Commands:")
    typer.echo("  ios-enroll device list            List connected devices")
    typer.echo("  ios-enroll device info            Show device details")
    typer.echo("\nExample: ios-enroll device list")


def _org_help() -> None:
    """Help message for incomplete org commands."""
    typer.secho("ios-enroll org - Organization management commands\n", fg=typer.colors.BLUE, bold=True)
    typer.echo("Commands:")
    typer.echo("  ios-enroll org list               List organizations")
    typer.echo("  ios-enroll org create             Create new organization")
    typer.echo("  ios-enroll org show               Show organization details")
    typer.echo("  ios-enroll org set-wifi           Configure WiFi for device")
    typer.echo("  ios-enroll org import             Import .organization file")
    typer.echo("  ios-enroll org generate           Generate supervision identity")
    typer.echo("\nExample: ios-enroll org list")


def _enroll_help() -> None:
    """Help message for incomplete enroll commands."""
    typer.secho("ios-enroll enroll - Enrollment commands\n", fg=typer.colors.BLUE, bold=True)
    typer.echo("Commands:")
    typer.echo("  ios-enroll enroll guided-enroll   Start guided enrollment workflow")
    typer.echo("  ios-enroll enroll make-supervised Prepare device for supervised enrollment")
    typer.echo("  ios-enroll enroll re-enroll        Re-enroll existing device")
    typer.echo("  ios-enroll enroll status          Check enrollment status")
    typer.echo("  ios-enroll enroll validate        Validate enrollment prerequisites")
    typer.echo("  ios-enroll enroll activate        Activate device")
    typer.echo("\nExample: ios-enroll enroll guided-enroll")


@device_app.callback(invoke_without_command=True)
def device_group(ctx: typer.Context):
    """Device management commands."""
    if ctx.invoked_subcommand is None:
        _device_help()


@org_app.callback(invoke_without_command=True)
def org_group(ctx: typer.Context):
    """Organization management commands."""
    if ctx.invoked_subcommand is None:
        _org_help()


@enroll_app.callback(invoke_without_command=True)
def enroll_group(ctx: typer.Context):
    """Enrollment commands."""
    if ctx.invoked_subcommand is None:
        _enroll_help()



@app.callback(invoke_without_command=True)
def cli_main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show version and exit"),
    gui: bool = typer.Option(False, "--gui", help="Launch the graphical user interface"),
):
    """Apple Configurator-like CLI for Linux.

    Manage iOS device enrollment with supervised pairing.
    """
    if version:
        typer.echo(f"ios-enroll {__version__}")
        raise typer.Exit(0)
    if gui:
        if ctx.invoked_subcommand is not None:
            typer.secho("--gui cannot be combined with another command", fg=typer.colors.RED)
            raise typer.Exit(1)
        from apple_device_cli.gui_qt import run_gui

        try:
            run_gui()
        except RuntimeError as e:
            typer.secho(f"GUI unavailable: {e}", fg=typer.colors.RED)
            raise typer.Exit(1) from e
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        typer.secho("ios-enroll - iOS device supervised enrollment CLI\n", fg=typer.colors.BLUE, bold=True)
        typer.echo("Manage iOS device enrollment with supervised pairing.\n")
        typer.echo("Commands:")
        typer.echo("  ios-enroll --gui                 Launch graphical user interface")
        typer.echo("  ios-enroll enroll guided-enroll  Guided interactive enrollment")
        typer.echo("  ios-enroll device list           List connected devices")
        typer.echo("  ios-enroll org list             List organizations")
        typer.echo("  ios-enroll --help               Show all commands")
        typer.echo("\nExamples:")
        typer.echo("  ios-enroll --gui                 Launch GUI")
        typer.echo("  ios-enroll enroll guided-enroll  Start guided enrollment")
        typer.echo("  ios-enroll device list          Show connected devices")
        typer.echo("  ios-enroll org create --name 'My Org'  Create organization")


@enroll_app.command("guided-enroll")
def interactive_enroll():
    """Guided supervised enrollment workflow matching Apple Configurator.

    Steps:
    1. Select device
    2. Configure MDM server
    3. Configure organization & supervision identity
    4. Configure WiFi (optional, headless-friendly)
    5. Select Setup Assistant skip panes
    6. Prepare device (decide whether erase is required)
    7. Apply configuration

    This mimics Apple Configurator's Prepare Assistant workflow.
    """
    from apple_device_cli.enrollment.skip_panes import PRESETS

    typer.secho("=== Apple Device Enrollment ===\n", fg=typer.colors.BLUE, bold=True)
    typer.echo("Following Apple Configurator workflow...\n")

    # Step 1: Select device
    devices = list_devices()
    if not devices:
        typer.secho("No devices found. Connect a device and try again.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo("Step 1: Select Device")
    typer.echo("-" * 40)
    typer.echo("Available devices:")
    for i, d in enumerate(devices):
        typer.echo(f"  [{i + 1}] {_display_udid(d.udid)}  ({d.device_name})")
    typer.echo()
    choice = typer.prompt("Select device number", default="1")
    try:
        selected = devices[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        typer.secho("Invalid selection", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"\nSelected: {selected.device_name} ({_display_udid(selected.udid)})")

    # Ensure device is paired/trusted before checking state or enrolling
    ensure_device_pairing(selected.udid)

    # Get device state
    try:
        lockdown = asyncio.run(_get_device_activation_state(selected.udid))
        activation_state = lockdown.get("ActivationState", "Unknown")
        has_cloud_config = lockdown.get("CloudConfigurationWasApplied", False)
        typer.echo(f"Activation state: {activation_state}")
        typer.echo(f"Cloud config applied: {has_cloud_config}")
    except Exception as e:
        typer.echo(f"Could not check device state: {e}")
        activation_state = None
        has_cloud_config = False

    # Step 2: MDM Server Configuration
    typer.echo("\nStep 2: MDM Server Configuration")
    typer.secho("-" * 40)
    typer.echo("Select enrollment type:")
    typer.echo("  [1] Do not enroll in MDM")
    typer.echo("  [2] Use existing MDM server")
    typer.echo("  [3] Configure new MDM server")
    enroll_choice = typer.prompt("Select option", default="2")

    mdm_url = None
    checkin_url = None
    mdm_topic = None

    if enroll_choice == "2":
        manager = OrganizationManager()
        orgs = manager.list_orgs()
        orgs_with_mdm = [o for o in orgs if o.mdm_url]
        if not orgs_with_mdm:
            typer.secho("No organizations with MDM URL found. Creating new server...", fg=typer.colors.YELLOW)
            enroll_choice = "3"
        else:
            typer.echo("\nAvailable MDM servers:")
            for i, o in enumerate(orgs_with_mdm):
                typer.echo(f"  [{i + 1}] {_display_name(o.name)} ({redact_url(o.mdm_url)})")
            choice = typer.prompt("Select MDM server", default="1")
            try:
                selected_org = orgs_with_mdm[int(choice) - 1]
                mdm_url = selected_org.mdm_url
                checkin_url = selected_org.checkin_url
                mdm_topic = selected_org.mdm_topic
            except (ValueError, IndexError) as exc:
                typer.secho("Invalid selection", fg=typer.colors.RED)
                raise typer.Exit(1) from exc

    if enroll_choice == "3":
        typer.echo("\nNew MDM Server Configuration:")
        mdm_url = typer.prompt("  Server URL (e.g. https://mdm.example.com/mdm)")
        checkin_url = typer.prompt("  Check-in URL (e.g. https://mdm.example.com/checkin)", default="")
        mdm_topic = typer.prompt("  MDM Topic", default="")

    if mdm_url:
        typer.echo(f"\nMDM Server URL: {redact_url(mdm_url)}")
        if checkin_url:
            typer.echo(f"Check-in URL: {redact_url(checkin_url)}")

    # Step 3: Organization Configuration (before WiFi - we need org for WiFi config)
    typer.echo("\nStep 3: Organization & Supervision Identity")
    typer.secho("-" * 40)
    manager = OrganizationManager()
    orgs = manager.list_orgs()
    existing_orgs = [o for o in orgs if o.cert_path and o.key_path]

    typer.echo("Organization options:")
    typer.echo("  [n] Create new organization")
    if existing_orgs:
        for i, o in enumerate(existing_orgs):
            typer.echo(f"  [{i + 1}] {_display_name(o.name)}")
    org_choice = typer.prompt("Select organization", default="n")

    org = None
    if org_choice.lower() == "n":
        name = typer.prompt("  Organization name")
        org_id = typer.prompt("  Organization ID (e.g. com.example)", default="")

        typer.echo("\n  Supervision identity:")
        typer.echo("    [1] Generate new identity")
        typer.echo("    [2] Use existing certificate/key")
        identity_choice = typer.prompt("Select option", default="1")

        if identity_choice == "1":
            valid_days_str = typer.prompt("  Certificate validity (days)", default=str(365 * 5))
            try:
                valid_days = int(valid_days_str)
            except ValueError:
                valid_days = 365 * 5

            cert_der, key_der = generate_org_identity(name, valid_days)
            org_dir = manager.orgs_dir / manager._sanitize_name(name)
            if org_dir.exists():
                existing_org = manager.get_org(name)
                if existing_org:
                    typer.secho(f"Organization '{name}' already exists and will be overwritten.", fg=typer.colors.YELLOW)
                else:
                    typer.secho(f"Directory '{org_dir}' already exists (name collision). Overwriting.", fg=typer.colors.YELLOW)
                shutil.rmtree(org_dir)
            org_dir.mkdir(parents=True, exist_ok=True)
            with open(org_dir / "cert.der", "wb") as f:
                f.write(cert_der)
            with open(org_dir / "key.der", "wb") as f:
                f.write(key_der)

            org = Organization(
                name=name,
                org_id=org_id or None,
                mdm_url=mdm_url,
                checkin_url=checkin_url or None,
                mdm_topic=mdm_topic or None,
                cert_path=str(org_dir / "cert.der"),
                key_path=str(org_dir / "key.der"),
            )
        else:
            cert_path = typer.prompt("  Path to certificate (DER)")
            key_path = typer.prompt("  Path to private key (DER)")
            org = Organization(
                name=name,
                org_id=org_id or None,
                mdm_url=mdm_url,
                checkin_url=checkin_url or None,
                mdm_topic=mdm_topic or None,
                cert_path=cert_path,
                key_path=key_path,
            )

        manager.save_org(org)
        typer.secho(f"Organization '{_display_name(name)}' saved.", fg=typer.colors.GREEN)
    else:
        try:
            org = existing_orgs[int(org_choice) - 1]
            if mdm_url and (org.mdm_url != mdm_url or org.checkin_url != checkin_url or org.mdm_topic != mdm_topic):
                org.mdm_url = mdm_url
                org.checkin_url = checkin_url
                org.mdm_topic = mdm_topic
                manager.save_org(org, overwrite=True)
            typer.echo(f"Using organization: {_display_name(org.name)}")
        except (ValueError, IndexError) as exc:
            typer.secho("Invalid organization selection", fg=typer.colors.RED)
            raise typer.Exit(1) from exc

    # Step 4: WiFi Configuration (now we have org to check for WiFi config)
    typer.echo("\nStep 4: WiFi Configuration")
    typer.secho("-" * 40)
    # Pre-check if selected org has WiFi config for default behavior
    org_wifi_path = Path(org.wifi_config_path).expanduser() if org and org.wifi_config_path else None
    org_wifi_available = org_wifi_path is not None and org_wifi_path.exists()
    typer.echo("Configure WiFi for headless enrollment (device will connect to WiFi before Setup Assistant):")
    if org_wifi_available:
        typer.echo("  [1] Use org WiFi config ({})".format(org_wifi_path.name if org_wifi_path else "wifi.mobileconfig"))
        typer.echo("  [2] Skip (WiFi not needed)")
        typer.echo("  [3] Enter WiFi credentials")
        typer.echo("  [4] Use different WiFi mobileconfig file")
        default_choice = "1"
    else:
        typer.echo("  [1] Skip (WiFi not needed)")
        typer.echo("  [2] Enter WiFi credentials")
        typer.echo("  [3] Use WiFi mobileconfig file")
        default_choice = "1"
    wifi_choice = typer.prompt("Select option", default=default_choice)

    wifi_ssid = None
    wifi_password = None
    wifi_encryption = "WPA"
    wifi_config = None

    if org_wifi_available and wifi_choice == "1":
        wifi_config = str(org_wifi_path)
        typer.echo(f"\nUsing org WiFi config: {redact_path(wifi_config)}")
    elif wifi_choice == "1":
        pass
    elif (org_wifi_available and wifi_choice == "3") or (not org_wifi_available and wifi_choice == "2"):
        wifi_ssid = typer.prompt("  WiFi SSID (network name)")
        wifi_password = typer.prompt("  WiFi password", hide_input=True)
        typer.echo("  Encryption type:")
        typer.echo("    [1] WPA/WPA2 (recommended)")
        typer.echo("    [2] WEP")
        typer.echo("    [3] None (open network)")
        enc_choice = typer.prompt("Select option", default="1")
        if enc_choice == "2":
            wifi_encryption = "WEP"
        elif enc_choice == "3":
            wifi_encryption = "None"
        typer.echo(f"\nWiFi: {wifi_ssid} ({wifi_encryption})")
    elif (org_wifi_available and wifi_choice == "4") or (not org_wifi_available and wifi_choice == "3"):
        wifi_config = _normalize_prompted_path(typer.prompt("  Path to WiFi mobileconfig file"))
        typer.echo(f"\nWiFi config: {redact_path(wifi_config)}")

    # Step 5: Skip Panes
    typer.echo("\nStep 5: Setup Assistant Skip Panes")
    typer.secho("-" * 40)
    typer.echo("Select skip panes preset:")
    typer.echo("  [1] minimal - Skip most panes for unattended setup")
    typer.echo("  [2] standard - Common enterprise configuration")
    typer.echo("  [3] all - Skip all applicable panes")
    typer.echo("  [4] custom - Configure individual panes")
    preset_choice = typer.prompt("Select preset", default="2")

    if preset_choice == "4":
        from apple_device_cli.enrollment.skip_panes import VALID_PANES

        typer.echo("\nAvailable panes to skip:")
        for pane in sorted(VALID_PANES):
            typer.echo(f"  - {pane}")
        panes_input = typer.prompt("\nEnter panes to skip (comma-separated, or 'all'):", default="")
        if panes_input.lower() == "all":
            skip_list = list(VALID_PANES)
        else:
            skip_list = [p.strip() for p in panes_input.split(",") if p.strip()]
    else:
        preset_map = {"1": "minimal", "2": "standard", "3": "all"}
        preset_name = preset_map.get(preset_choice, "standard")
        skip_list = PRESETS.get(preset_name, PRESETS["standard"])

    typer.echo(f"\nSkipping {len(skip_list)} panes: {', '.join(skip_list[:5])}{'...' if len(skip_list) > 5 else ''}")

    # Step 6: Device Preparation
    typer.echo("\nStep 6: Device Preparation")
    typer.secho("-" * 40)

    # Get full device state for smart erase decision
    try:
        lockdown = asyncio.run(_get_device_activation_state(selected.udid))
        is_supervised = lockdown.get("IsSupervised", False)
        has_cloud_config = lockdown.get("CloudConfigurationWasApplied", False)
    except (ConnectionError, TimeoutError):
        is_supervised = False
        has_cloud_config = False

    needs_erase = False

    # State machine: Determine if erase is needed based on explicit device state combinations
    # Possible states (with supervision implications):
    # 1. Fresh (Unactivated, not supervised, no cloud config) → No erase needed
    # 2. Activated clean (Activated, not supervised, no cloud config) → No erase needed
    # 3. Already enrolled (Activated, supervised, cloud config) → ERASE REQUIRED to re-enroll
    # 4. Partial config (any combo with cloud config but not supervised) → ERASE REQUIRED
    
    if has_cloud_config:
        # Any device with cloud config needs erase to re-enroll (regardless of supervised state)
        needs_erase = True
        typer.secho("Device already has cloud configuration applied.", fg=typer.colors.YELLOW)
        typer.echo(f"  State: supervised={is_supervised}, cloud_config_applied=True")
        typer.echo("  Must erase to re-enroll with different configuration.")
        typer.echo("  Alternatively, use 'enroll re-enroll' to clear only cloud config.")
        if not typer.confirm("Erase and restore device now?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW)
            raise typer.Exit(1)
    elif is_supervised:
        # Supervised without cloud config is impossible state (shouldn't happen)
        typer.secho("Device is supervised but has no cloud config (unexpected state).", fg=typer.colors.YELLOW)
        needs_erase = True
        if not typer.confirm("Erase device to reset to clean state?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW)
            raise typer.Exit(1)
    elif activation_state == "Activated":
        # Activated but clean (no cloud config, not supervised) - no erase needed
        typer.secho("Device is activated and clean. Ready for supervision.", fg=typer.colors.GREEN)
        needs_erase = False
    else:
        # Unactivated and clean - no erase needed
        typer.secho("Fresh device detected (unactivated, clean). Applying configuration directly.", fg=typer.colors.GREEN)
        needs_erase = False

    if needs_erase:
        typer.secho("Device needs erase before re-enrollment.", fg=typer.colors.YELLOW)
        typer.echo("  Use pymobiledevice3 to erase:")
        typer.echo(f"    pymobiledevice3 restore update --udid {selected.udid}")
        typer.echo("  After erase completes, re-connect the device and run this enrollment again.")
        typer.secho("Aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    # Step 7: Apply configuration
    typer.echo("\nStep 7: Apply Configuration")
    typer.secho("-" * 40)
    typer.echo("Enrolling device as supervised...")

    # Progress callback for enrollment steps
    def progress_callback(msg: str) -> None:
        typer.echo(f"  {sanitize_text(msg)}")

    try:
        if not org.cert_path or not org.key_path:
            typer.secho("Selected organization is missing a certificate or private key.", fg=typer.colors.RED)
            raise typer.Exit(1)
        result = make_supervised(
            cert_path=org.cert_path,
            key_path=org.key_path,
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            wifi_ssid=wifi_ssid,
            wifi_password=wifi_password,
            wifi_encryption=wifi_encryption,
            wifi_config=wifi_config,
            mdm_mobileconfig=org.mdm_mobileconfig_path,
            udid=selected.udid,
            progress_callback=progress_callback,
        )
        typer.secho("\n" + "=" * 50, fg=typer.colors.GREEN, bold=True)
        if result.success:
            typer.secho("  Device is now supervised and enrolled!", fg=typer.colors.GREEN, bold=True)
        else:
            typer.secho("  Enrollment completed with errors", fg=typer.colors.YELLOW, bold=True)
        typer.secho("=" * 50, fg=typer.colors.GREEN, bold=True)
        typer.echo(f"\n  Organization: {_display_name(org.name)}")
        typer.echo(f"  Device UDID: {_display_udid(result.device_udid)}")
        typer.echo(f"  Supervised: {result.supervised}")
        typer.echo(f"  MDM Enrolled: {result.mdm_enrolled}")
        typer.echo(f"  WiFi Installed: {result.wifi_installed}")
        if org.mdm_url:
            typer.echo(f"  MDM Server URL: {redact_url(org.mdm_url)}")
        if result.cloud_config and result.cloud_config.get("MDMServerURL"):
            typer.echo(f"  Cloud Config MDM URL: {redact_url(result.cloud_config['MDMServerURL'])}")
        typer.echo(f"  Skip panes: {len(skip_list)} configured")
        if not result.mdm_enrolled and org.mdm_url:
            typer.secho("\n NOTE: MDM profile stored for post-setup installation.", fg=typer.colors.CYAN)
            typer.echo(" Device will install MDM profile during Setup Assistant.")
        if result.errors:
            typer.secho("\n  Errors:", fg=typer.colors.YELLOW)
            for error in result.errors:
                typer.echo(f"    - {sanitize_text(error)}")
        typer.echo("\n  Connect device to power and wait for Setup Assistant...")
    except AppleDeviceError as e:
        typer.secho(f"Enrollment failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        raise typer.Exit(1)


def _prompt_for_udid(udid: str | None, allow_empty: bool = False) -> DeviceInfo | None:
    """Resolve a device selection, prompting the user when needed."""
    if udid:
        info = get_device_info(udid)
        if info and info.device_type not in ("", "Unknown"):
            return info
        return DeviceInfo(
            udid=udid,
            device_name="Unknown",
            device_type="Unknown",
            build_version="Unknown",
            firmware_version="Unknown",
        )

    devices = list_devices()

    if not devices:
        if allow_empty:
            return None
        typer.secho("No devices found. Connect a device and try again.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo("Available devices:")
    for i, device in enumerate(devices, start=1):
        typer.echo(f"  [{i}] {_display_udid(device.udid)}  ({device.device_name})")
    typer.echo()
    choice = typer.prompt("Select device number", default="1")
    try:
        return devices[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        typer.secho("Invalid selection", fg=typer.colors.RED)
        raise typer.Exit(1) from exc


async def _get_device_activation_state(udid: str):
    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = await create_using_usbmux(serial=udid)
    return lockdown.all_values


@device_app.command("list")
def device_list(
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed device info"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List connected devices."""
    try:
        devices = list_devices()
        if not devices:
            typer.secho("No devices found", fg=typer.colors.YELLOW)
            return
        if json_output:
            output = [{
                "udid": d.udid,
                "name": d.device_name,
                "type": d.device_type,
                "ios_version": d.firmware_version,
                "build_version": d.build_version,
                "ecid": d.ecid,
            } for d in devices]
            typer.echo(json.dumps(output, indent=2))
        else:
            for d in devices:
                if verbose:
                    typer.echo(f"{_display_udid(d.udid)}\t{d.device_name}\t{d.device_type}\t{d.firmware_version}\t{d.build_version}")
                    if d.ecid:
                        typer.echo(f"  ECID: {_display_udid(d.ecid)}")
                else:
                    typer.echo(f"{_display_udid(d.udid)}\t{d.device_name}")
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("info")
def device_info(
    udid: str = typer.Option(None, "--udid"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Get device info."""
    if not udid:
        devices = list_devices()
        if not devices:
            typer.secho("No device found", fg=typer.colors.RED)
            raise typer.Exit(1)
        typer.echo("Multiple devices found. Use --udid to specify.\n")
        for i, d in enumerate(devices):
            typer.echo(f"  [{i + 1}] {_display_udid(d.udid)}  ({d.device_name})")
        typer.echo()
        choice = typer.prompt("Select device number", default="1")
        try:
            udid = devices[int(choice) - 1].udid
        except (ValueError, IndexError) as exc:
            typer.secho("Invalid selection", fg=typer.colors.RED)
            raise typer.Exit(1) from exc
    ensure_device_pairing(udid)
    info = get_device_info(udid)
    if info:
        if json_output:
            output = {
                "udid": info.udid,
                "name": info.device_name,
                "type": info.device_type,
                "ios_version": info.firmware_version,
                "build_version": info.build_version,
                "ecid": info.ecid,
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.echo(f"UDID: {_display_udid(info.udid)}")
            typer.echo(f"Name: {info.device_name}")
            typer.echo(f"Type: {info.device_type}")
            typer.echo(f"iOS: {info.firmware_version} ({info.build_version})")
            if info.ecid:
                typer.echo(f"ECID: {_display_udid(info.ecid)}")
    else:
        typer.secho(f"Device not found: {_display_udid(udid)}", fg=typer.colors.RED)


@device_app.command("restore")
def device_restore(
    udid: str = typer.Option(None, "--udid", help="Target device UDID"),
    ipsw: str = typer.Option(
        None, "--ipsw",
        help="Path to a local .ipsw file (skips the version dropdown).",
    ),
    list_versions: bool = typer.Option(
        False, "--list-versions",
        help="Print the signed iOS versions for the device and exit.",
    ),
    cache_dir: str = typer.Option(
        None, "--cache-dir",
        help="Override firmware cache location (else uses $IOS_ENROLL_CACHE_DIR or ~/.config/ios-enroll/config.json or ~/.cache/ios-enroll/firmware/).",
    ),
    show_cache: bool = typer.Option(
        False, "--show-cache",
        help="Print the current cache state and exit.",
    ),
    clear_cache: bool = typer.Option(
        False, "--clear-cache",
        help="Remove all downloaded IPSW files from the cache and exit.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip the confirmation prompt (for scripts).",
    ),
):
    """Restore a device to a signed iOS version (or a local .ipsw file).

    The device must be in Normal mode and trusted by the host. Older
    iPads may take 45-60+ minutes for a full restore. To survive the
    agent's foreground terminal timeout, run this command via
    background=true + notify_on_complete=true, or in a tmux/screen
    window.
    """

    # --- Cache resolution and --show-cache / --clear-cache short-circuits ---
    try:
        resolved_cache = resolve_cache_dir(override=cache_dir)
    except RestoreEngineError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if show_cache:
        state = cache_state(resolved_cache)
        typer.echo(f"Cache: {state['path']}")
        typer.echo(f"  size: {state['size_bytes']:,} bytes")
        typer.echo(f"  IPSW count: {state['ipsw_count']}")
        for f in state['ipsw_files']:
            typer.echo(f"    - {f}")
        raise typer.Exit(0)

    if clear_cache:
        state = cache_state(resolved_cache)
        if not state['ipsw_files']:
            typer.echo("Cache is already empty.")
            raise typer.Exit(0)
        if not yes and sys.stdin.isatty():
            confirm = typer.confirm(
                f"Delete {state['ipsw_count']} IPSW files from {state['path']}?",
                default=False,
            )
            if not confirm:
                typer.secho("Cancelled.", fg=typer.colors.YELLOW)
                raise typer.Exit(1)
        for f in state['ipsw_files']:
            (resolved_cache / f).unlink()
        typer.echo(f"Removed {state['ipsw_count']} IPSW files.")
        raise typer.Exit(0)

    if not udid and not ipsw:
        typer.secho(
            "Either --udid or --ipsw is required.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(1)

    # --- Resolve product_type from the device (only if we need it) ---
    product_type: str | None = None
    if not ipsw:
        try:
            product_type = get_product_type_for_udid(udid)
        except RestoreEngineError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Device {udid}: {product_type}")

    # --- --list-versions short-circuit ---
    if list_versions:
        if product_type is None:
            typer.secho(
                "--list-versions requires --udid (so we can read the ProductType).",
                fg=typer.colors.RED, err=True,
            )
            raise typer.Exit(1)
        try:
            versions = list_signed_versions(product_type)
        except RestoreEngineError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        for v in versions:
            typer.echo(f"{v.display_label}  {v.url}")
        raise typer.Exit(0)

    # --- Resolve the IPSW to use ---
    ipsw_path: Path
    if ipsw:
        ipsw_path = Path(ipsw).expanduser()
        if not ipsw_path.exists():
            typer.secho(f"IPSW not found: {ipsw_path}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    else:
        # CLI doesn't have an interactive version picker. The user
        # must use --ipsw (or the GUI) to actually pick a version.
        typer.secho(
            "The CLI doesn't pick a version interactively. Pass "
            "--ipsw <local-path>, or use the GUI Restore tab to pick "
            "from the dropdown. (Use --list-versions to see what's "
            "available for this device.)",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(1)

    if not udid:
        typer.secho("--udid is required when --ipsw is used.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    # --- Confirmation gate (engine itself passes -y to idevicerestore) ---
    if not yes and sys.stdin.isatty():
        typer.secho(
            f"\nAbout to ERASE and RESTORE {udid} to {ipsw_path.name}.",
            fg=typer.colors.YELLOW, bold=True,
        )
        typer.secho(
            "This will delete all data on the device. Older iPads "
            "may take 45-60+ minutes — run via tmux/screen or the agent's "
            "background mode to survive the terminal timeout.",
            fg=typer.colors.RED,
        )
        confirm = typer.confirm("Continue?", default=False)
        if not confirm:
            typer.secho("Cancelled.", fg=typer.colors.YELLOW)
            raise typer.Exit(1)

    # --- Run the restore (no subprocess timeout) ---
    if not shutil.which("idevicerestore"):
        typer.secho(
            "idevicerestore is not on PATH. The pymobiledevice3 "
            "fallback is not implemented in this iteration. Install "
            "libimobiledevice (brew install libimobiledevice) and try again.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(1)

    def _progress(event: ProgressEvent) -> None:
        typer.echo(f"  {event.text}")

    typer.echo(f"Cache: {resolved_cache}")
    typer.echo(
        f"Log:   {resolved_cache / 'logs' / f'restore_{udid}.log'}"
    )
    typer.echo(
        "Restore started. This can take 15-60+ minutes for older iPads."
    )
    typer.echo(
        "Use Ctrl-C to abort (the device will not be left in a bad state)."
    )

    result = restore_device(
        udid=udid,
        ipsw_path=ipsw_path,
        cache_dir=resolved_cache,
        progress_callback=_progress,
    )

    if result.success:
        typer.secho("Restore completed successfully.", fg=typer.colors.GREEN, bold=True)
        raise typer.Exit(0)
    typer.secho(f"Restore failed: {result.error}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@org_app.command("list")
def org_list(
    verbose: bool = typer.Option(False, "--verbose", help="Show MDM URL and certificate status"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List organizations."""
    manager = OrganizationManager()
    orgs = manager.list_orgs()
    if not orgs:
        typer.echo("No organizations stored.")
        typer.echo(f"  Location: {manager.orgs_dir}")
        return
    if json_output:
        output = []
        for org in orgs:
            org_data = {
                "name": org.name,
                "org_id": org.org_id,
                "mdm_url": org.mdm_url,
                "has_cert": org.cert_path is not None and Path(org.cert_path).exists(),
                "has_key": org.key_path is not None and Path(org.key_path).exists(),
            }
            output.append(org_data)
        typer.echo(json.dumps(output, indent=2))
    else:
        typer.echo(f"Organizations in: {redact_path(manager.orgs_dir)}")
        for org in orgs:
            typer.echo(f"  {_display_name(org.name)}")
            if verbose:
                if org.org_id:
                    typer.echo(f"    ID: {_display_org_id(org.org_id)}")
                if org.mdm_url:
                    typer.echo(f"    MDM URL: {redact_url(org.mdm_url)}")
                has_cert = org.cert_path is not None and Path(org.cert_path).exists()
                has_key = org.key_path is not None and Path(org.key_path).exists()
                typer.echo(f"    Cert: {'Yes' if has_cert else 'No'}")
                typer.echo(f"    Key: {'Yes' if has_key else 'No'}")
            elif org.org_id:
                typer.echo(f"    ID: {_display_org_id(org.org_id)}")


@org_app.command("create")
def org_create(
    name: str = typer.Option(..., "--name"),
    org_id: str = typer.Option(None, "--org-id"),
    address: str = typer.Option(None, "--address"),
    phone: str = typer.Option(None, "--phone"),
    email: str = typer.Option(None, "--email"),
    mdm_url: str = typer.Option(None, "--mdm-url"),
    checkin_url: str = typer.Option(None, "--checkin-url"),
    mdm_topic: str = typer.Option(None, "--mdm-topic"),
    mdm_description: str = typer.Option(None, "--mdm-description"),
    cert: str = typer.Option(None, "-C", "--cert"),
    key: str = typer.Option(None, "-K", "--key"),
    wifi_config: str = typer.Option(None, "--wifi-config", help="Path to WiFi mobileconfig file"),
):
    """Create organization with MDM server configuration.

    Example:
        ios-enroll org create --name "My Org" --mdm-url https://mdm.example.com/mdm \\
            --checkin-url https://mdm.example.com/checkin --mdm-topic com.example.mdm
    """
    manager = OrganizationManager()
    try:
        result = create_org(
            manager=manager,
            name=name,
            org_id=org_id,
            address=address,
            phone=phone,
            email=email,
            mdm_url=mdm_url,
            checkin_url=checkin_url,
            mdm_topic=mdm_topic,
            mdm_description=mdm_description,
            cert=cert,
            key=key,
            wifi_config=wifi_config,
        )
    except OrgAlreadyExistsError as e:
        typer.secho(f"Create failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        raise typer.Exit(1) from e
    except ValueError as e:
        typer.secho(f"Create failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        raise typer.Exit(1) from e
    typer.secho(f"Created organization: {_display_name(result.name)}", fg=typer.colors.GREEN)
    if result.mdm_url:
        typer.echo(f"  MDM URL: {redact_url(result.mdm_url)}")
    if result.checkin_url:
        typer.echo(f"  Check-in URL: {redact_url(result.checkin_url)}")
    if result.mdm_topic:
        typer.echo(f"  MDM Topic: {_display_org_id(result.mdm_topic)}")
    if result.wifi_config_path:
        typer.echo(f"  WiFi Config: {redact_path(result.wifi_config_path)}")


@org_app.command("delete")
def org_delete(name: str = typer.Option(..., "--name")):
    """Delete organization."""
    manager = OrganizationManager()
    try:
        delete_org(manager, name)
    except OrgNotFoundError:
        typer.secho(f"Organization not found: {_display_name(name)}", fg=typer.colors.RED)
        return
    typer.secho(f"Deleted organization: {_display_name(name)}", fg=typer.colors.GREEN)


@org_app.command("set-cert")
def org_set_cert(
    name: str = typer.Option(..., "--name"),
    cert: str = typer.Option(..., "-C", "--cert"),
) -> None:
    """Set certificate for organization."""
    _set_org_field(name, "cert_path", str(Path(cert).resolve()), "certificate")


@org_app.command("set-key")
def org_set_key(
    name: str = typer.Option(..., "--name"),
    key: str = typer.Option(..., "-K", "--key"),
) -> None:
    """Set private key for organization."""
    _set_org_field(name, "key_path", str(Path(key).resolve()), "private key")


@org_app.command("set-mdm-url")
def org_set_mdm_url(
    name: str = typer.Option(..., "--name"),
    mdm_url: str = typer.Option(..., "--mdm-url"),
) -> None:
    """Set MDM server URL for organization."""
    _set_org_field(name, "mdm_url", mdm_url, "MDM URL")


@org_app.command("set-checkin-url")
def org_set_checkin_url(
    name: str = typer.Option(..., "--name"),
    checkin_url: str = typer.Option(..., "--checkin-url"),
) -> None:
    """Set SCEP check-in URL for organization."""
    _set_org_field(name, "checkin_url", checkin_url, "check-in URL")


@org_app.command("set-mdm-topic")
def org_set_mdm_topic(
    name: str = typer.Option(..., "--name"),
    mdm_topic: str = typer.Option(..., "--mdm-topic"),
) -> None:
    """Set MDM topic for organization."""
    _set_org_field(name, "mdm_topic", mdm_topic, "MDM topic")


@org_app.command("show")
def org_show(name: str = typer.Option(..., "--name")):
    """Show organization details."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(f"Name: {_display_name(org.name)}")
    if org.org_id:
        typer.echo(f"ID: {_display_org_id(org.org_id)}")
    if org.address:
        typer.echo(f"Address: {redact_address(org.address)}")
    if org.phone:
        typer.echo(f"Phone: {redact_phone(org.phone)}")
    if org.email:
        typer.echo(f"Email: {redact_email(org.email)}")
    if org.mdm_url:
        typer.echo(f"MDM URL: {redact_url(org.mdm_url)}")
    if org.checkin_url:
        typer.echo(f"Check-in URL: {redact_url(org.checkin_url)}")
    if org.mdm_topic:
        typer.echo(f"MDM Topic: {_display_org_id(org.mdm_topic)}")
    if org.mdm_description:
        typer.echo(f"MDM Description: {org.mdm_description}")
    typer.echo(f"Created: {org.created_at}")
    typer.echo(f"Cert: {redact_path(org.cert_path) if org.cert_path else 'Not set'}")
    typer.echo(f"Key: {redact_path(org.key_path) if org.key_path else 'Not set'}")
    if org.cert_path and Path(org.cert_path).exists():
        try:
            cert_info = load_cert_info(Path(org.cert_path).read_bytes())
            cn = cert_info.get("2.5.4.3", None)
            if cn:
                typer.echo(f"Cert CN: {cn}")
        except (OSError, ValueError):
            pass  # Cert info is optional; non-fatal


@org_app.command("import")
def org_import(
    path: str = typer.Option(..., "--path"),
    password: str = typer.Option("", "-p", "--password"),
):
    """Import organization from Apple Configurator .organization file, directory, or zip."""
    manager = OrganizationManager()
    try:
        org = import_org(manager, path, password)
    except ValueError as e:
        typer.secho(f"Import failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        return
    except Exception as e:
        typer.secho(f"Import failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        return
    typer.secho(f"Imported: {_display_name(org.name)}", fg=typer.colors.GREEN)
    typer.echo(f"  Cert: {'Yes' if org.cert_path else 'No'}")
    typer.echo(f"  Key: {'Yes' if org.key_path else 'No'}")
    if org.org_id:
        typer.echo(f"  ID: {org.org_id}")


@org_app.command("import-mobileconfig")
def org_import_mobileconfig(
    path: str = typer.Option(..., "--path"),
):
    """Import organization from MDM .mobileconfig file."""
    manager = OrganizationManager()
    try:
        org = import_mobileconfig(manager, path)
    except ValueError as e:
        typer.secho(f"Import failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        return
    except Exception as e:
        typer.secho(f"Import failed: {sanitize_text(str(e))}", fg=typer.colors.RED)
        return
    typer.secho(f"Imported: {_display_name(org.name)}", fg=typer.colors.GREEN)
    typer.echo(f"  MDM URL: {redact_url(org.mdm_url) if org.mdm_url else 'Not set'}")
    typer.echo(f"  Check-in URL: {redact_url(org.checkin_url) if org.checkin_url else 'Not set'}")
    typer.echo(f"  Cert: {'Yes' if org.cert_path else 'No'}")
    typer.echo(f"  Key: {'Yes' if org.key_path else 'No'}")


@org_app.command("set-wifi")
def org_set_wifi(
    name: str = typer.Option(..., "--name"),
    path: str = typer.Option(..., "--path"),
):
    """Attach a WiFi mobileconfig to an organization.

    The WiFi config will be installed on devices during supervised enrollment.

    Example:
        ios-enroll org set-wifi --name "Capital Candy Company" --path wifi.mobileconfig
    """
    from apple_device_cli.cli_actions import WifiConfigInvalidError, WifiConfigNotFoundError

    manager = OrganizationManager()
    try:
        result = set_org_wifi(manager, name, path)
    except OrgNotFoundError:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        raise typer.Exit(1)
    except WifiConfigNotFoundError:
        typer.secho(f"WiFi config file not found: {redact_path(path)}", fg=typer.colors.RED)
        raise typer.Exit(1)
    except WifiConfigInvalidError:
        typer.secho(f"Invalid mobileconfig: {redact_path(path)} is not a valid plist", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho(f"WiFi config attached to: {_display_name(result.name)}", fg=typer.colors.GREEN)
    typer.echo(f"  File: {redact_path(result.wifi_config_path)}")


@org_app.command("export")
def org_export(name: str = typer.Option(..., "--name"), path: str = typer.Option(..., "--path")):
    """Export organization to directory or zip."""
    manager = OrganizationManager()
    if manager.export_org(name, path):
        typer.secho(f"Exported '{_display_name(name)}' to {redact_path(path)}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Organization not found: {_display_name(name)}", fg=typer.colors.RED)


@org_app.command("generate")
def org_generate(
    name: str = typer.Option(..., "--name"),
    org_id: str = typer.Option(None, "--org-id"),
    mdm_url: str = typer.Option(None, "--mdm-url"),
    checkin_url: str = typer.Option(None, "--checkin-url"),
    mdm_topic: str = typer.Option(None, "--mdm-topic"),
    mdm_description: str = typer.Option(None, "--mdm-description"),
    valid_days: int = typer.Option(365 * 5, "--valid-days"),
):
    """Generate a new supervising identity for an organization.

    Creates a self-signed certificate and private key for the organization,
    then saves the org with the specified MDM server configuration.

    Example:
        ios-enroll org generate --name "My Org" --mdm-url https://mdm.example.com/mdm \
            --checkin-url https://mdm.example.com/checkin --mdm-topic com.example.mdm
    """
    manager = OrganizationManager()
    existing = manager.get_org(name)
    if existing and existing.cert_path and existing.key_path:
        if not typer.confirm(f"Organization '{name}' already has a cert/key. Overwrite?"):
            return

    result = generate_org(
        manager=manager,
        name=name,
        org_id=org_id,
        mdm_url=mdm_url,
        checkin_url=checkin_url,
        mdm_topic=mdm_topic,
        mdm_description=mdm_description,
        valid_days=valid_days,
    )

    typer.secho(f"Generated identity for: {_display_name(result.name)}", fg=typer.colors.GREEN)
    if result.mdm_url:
        typer.echo(f"  MDM URL: {redact_url(result.mdm_url)}")
    if result.checkin_url:
        typer.echo(f"  Check-in URL: {redact_url(result.checkin_url)}")
    if result.mdm_topic:
        typer.echo(f"  MDM Topic: {_display_org_id(result.mdm_topic)}")


@enroll_app.command("make-supervised")
def enroll_make_supervised(
    udid: str = typer.Option(None, "--udid"),
    org_name: str = typer.Option(..., "--org-name"),
    skip_preset: str = typer.Option(None, "--skip-preset"),
    skip: list[str] = typer.Option([], "--skip"),
    wifi_ssid: str = typer.Option(None, "--wifi-ssid"),
    wifi_password: str = typer.Option(None, "--wifi-password"),
    wifi_encryption: str = typer.Option("WPA", "--wifi-encryption"),
    mdm_unremovable: bool = typer.Option(False, "--mdm-unremovable"),
    wifi_config: str = typer.Option(None, "--wifi-config"),
    mdm_mobileconfig: str = typer.Option(None, "--mdm-mobileconfig", help="Path to MDM enrollment mobileconfig"),
    fail_on_mdm_error: bool = typer.Option(True, "--fail-on-mdm-error/--no-fail-on-mdm-error"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show progress updates"),
):
    """Make device supervised with optional MDM enrollment.

    Uses organization cert/key for supervision identity and optionally enrolls
    the device in MDM. Device UDID can be specified with --udid or selected
    interactively.
    """
    device = _prompt_for_udid(udid)
    if not device:
        typer.secho("No device selected", fg=typer.colors.RED)
        raise typer.Exit(1)
    manager = OrganizationManager()
    org = manager.get_org(org_name)
    if not org:
        typer.secho(f"Organization not found: {org_name}", fg=typer.colors.RED)
        raise typer.Exit(1)
    if not org.cert_path or not org.key_path:
        typer.secho(f"Organization '{org_name}' missing cert or key", fg=typer.colors.RED)
        raise typer.Exit(1)
    try:
        skip_list = resolve_skip_panes(skip_preset, skip)
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        return

    # Determine WiFi config: CLI options take priority, fall back to org's wifi_config_path
    effective_wifi_config = wifi_config
    effective_wifi_ssid = wifi_ssid
    effective_wifi_password = wifi_password

    if not effective_wifi_config and not effective_wifi_ssid and org.wifi_config_path:
        # No CLI wifi options provided, offer org's known wifi config
        wifi_path = Path(org.wifi_config_path)
        if wifi_path.exists():
            typer.echo(f"Organization has known WiFi config: {wifi_path.name}")
            # Use org's WiFi config by default (auto-install in non-interactive, ask in interactive)
            if sys.stdin.isatty():
                include_wifi = typer.confirm("Include WiFi profile in enrollment?", default=True)
            else:
                include_wifi = True  # Non-interactive: default to yes
            if include_wifi:
                effective_wifi_config = str(wifi_path)
                typer.echo(f"  Will install WiFi profile: {wifi_path.name}")

    # Set up progress callback if verbose mode
    progress_callback: Callable[[str], None] | None = None
    if verbose:
        def _progress_callback(msg: str) -> None:
            typer.echo(f"  {sanitize_text(msg)}")
        progress_callback = _progress_callback
        typer.echo(f"Supervised enrollment for: {device.device_name} ({_display_udid(device.udid)})")

    try:
        # Use CLI-provided MDM mobileconfig, or fall back to org's configured path
        effective_mdm_mobileconfig = mdm_mobileconfig or org.mdm_mobileconfig_path

        result = make_supervised(
            cert_path=org.cert_path,
            key_path=org.key_path,
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            wifi_ssid=effective_wifi_ssid,
            wifi_password=effective_wifi_password,
            wifi_encryption=wifi_encryption,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            mdm_unremovable=mdm_unremovable,
            wifi_config=effective_wifi_config,
            mdm_mobileconfig=effective_mdm_mobileconfig,
            udid=device.udid,
            fail_on_mdm_error=fail_on_mdm_error,
            progress_callback=progress_callback,
        )
        if result.success:
            typer.secho("Device is now supervised", fg=typer.colors.GREEN)
            typer.echo(f" UDID: {_display_udid(result.device_udid)}")
            typer.echo(f" Supervised: {'Yes' if result.supervised else 'No'}")
            typer.echo(f" MDM Enrolled: {'Yes' if result.mdm_enrolled else 'No'}")
            typer.echo(f" WiFi Installed: {'Yes' if result.wifi_installed else 'No'}")
        else:
            typer.secho("Enrollment completed with errors:", fg=typer.colors.YELLOW)
            for error in result.errors:
                typer.echo(f"  - {sanitize_text(error)}")
    except AppleDeviceError as e:
        typer.secho(f"Error: {sanitize_text(str(e))}", fg=typer.colors.RED)


@enroll_app.command("re-enroll")
def enroll_reenroll(
    udid: str = typer.Option(None, "--udid"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
):
    """Erase device cloud config to allow fresh re-enrollment.

    This removes the current supervised configuration from the device,
    allowing it to be re-enrolled with a new or existing organization.
    Use this when you need to change the MDM server or organization.
    """
    from apple_device_cli.enrollment.supervised import erase_device_for_reenrollment

    device = _prompt_for_udid(udid)
    if not device:
        typer.secho("No device selected", fg=typer.colors.RED)
        raise typer.Exit(1)

    if not force:
        typer.echo()
        typer.secho("WARNING: This will remove supervised configuration from the device.", fg=typer.colors.YELLOW)
        typer.echo(f"  Device: {device.device_name} ({_display_udid(device.udid)})")
        typer.echo()
        confirm = typer.confirm("Continue with re-enrollment preparation?")
        if not confirm:
            typer.secho("Cancelled.", fg=typer.colors.YELLOW)
            raise typer.Exit(1)

    try:
        typer.echo("Erasing cloud configuration...")
        erase_device_for_reenrollment(device.udid)
        typer.secho("Device cloud config erased. Ready for fresh enrollment.", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)


@enroll_app.command("status")
def enroll_status(
    udid: str = typer.Option(None, "--udid"),
):
    """Show enrollment status of a connected device.

    Displays current activation state, supervision status, and MDM enrollment
    information for the selected device.
    """
    from apple_device_cli.enrollment.supervised import get_device_enrollment_state

    device = _prompt_for_udid(udid)
    if not device:
        typer.secho("No device selected", fg=typer.colors.RED)
        return

    typer.echo(f"Device: {device.device_name} ({_display_udid(device.udid)})")
    typer.secho("-" * 40)

    try:
        state = get_device_enrollment_state(device.udid)
        if "error" in state:
            typer.secho(f"Could not get device state: {state['error']}", fg=typer.colors.RED)
            return

        typer.echo(f"  Activation State: {state.get('activation_state', 'Unknown')}")
        typer.echo(f"  Supervised: {state.get('is_supervised', False)}")
        typer.echo(f"  Cloud Config Applied: {state.get('cloud_config_applied', False)}")
        if state.get('org_name'):
            typer.echo(f"  Organization: {_display_name(state['org_name'])}")
        if state.get('org_magic'):
            typer.echo(f"  Organization ID: {_display_org_id(state['org_magic'])}")
        typer.echo(f"  Was Mandatorily Unpaired: {state.get('was_mandatorily_unpaired', False)}")
    except (KeyboardInterrupt, typer.Abort):
        raise
    except Exception as e:
        typer.secho(f"Error getting device status: {sanitize_text(str(e))}", fg=typer.colors.RED)


@enroll_app.command("validate")
def enroll_validate(
    org_name: str = typer.Option(None, "--org-name"),
    mdm_url: str = typer.Option(None, "--mdm-url"),
    check_mdm: bool = typer.Option(False, "--check-mdm", help="Verify MDM server is reachable"),
):
    """Validate enrollment prerequisites without touching devices.

    Checks that the organization exists with valid cert/key and optionally
    verifies the MDM server is reachable.
    """
    if not org_name:
        org_name = typer.prompt("Organization name (required)")
        if not org_name:
            typer.secho("Validation cancelled: organization name required", fg=typer.colors.YELLOW)
            return

    from apple_device_cli.enrollment.supervised import validate_enrollment_prerequisites

    manager = OrganizationManager()
    org = manager.get_org(org_name)
    if not org:
        typer.secho(f"Organization not found: {org_name}", fg=typer.colors.RED)
        return

    typer.echo(f"Validating organization: {_display_name(org_name)}")
    typer.secho("-" * 40)

    # Determine MDM URL to check
    target_mdm_url = mdm_url or org.mdm_url

    errors = validate_enrollment_prerequisites(
        cert_path=org.cert_path,
        key_path=org.key_path,
        org_name=org.name,
        mdm_url=target_mdm_url,
        check_mdm_reachability=check_mdm,
    )

    if not errors:
        typer.secho("All prerequisites valid!", fg=typer.colors.GREEN)
        typer.echo(f"  Certificate: {redact_path(org.cert_path)}")
        typer.echo(f"  Private Key: {redact_path(org.key_path)}")
        if target_mdm_url:
            typer.echo(f"  MDM URL: {redact_url(target_mdm_url)}")
    else:
        typer.secho("Validation failed:", fg=typer.colors.RED)
        for error in errors:
            typer.echo(f"  - {sanitize_text(error)}")


@enroll_app.command("activate")
def enroll_activate(udid: str = typer.Option(None, "--udid")):
    """Activate device."""
    try:
        activate_device(udid)
        typer.secho("Device activated", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {sanitize_text(str(e))}", fg=typer.colors.RED)


def main():
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    main()
