from pathlib import Path
import shutil
import time

import asyncio
import typer

from apple_device_cli import __version__
from apple_device_cli.device.connection import (
    ensure_device_pairing,
    get_device_info,
    list_devices,
    wait_for_udid_in_usbmux,
)
from apple_device_cli.device.info import DeviceInfo
from apple_device_cli.restore.erase import (
    enter_recovery_mode,
    erase_device,
    get_signed_firmwares,
    resolve_firmware_url,
    restore_device,
    update_device,
    InsufficientSpaceError,
)
from apple_device_cli.orgs.manager import OrganizationManager, Organization
from apple_device_cli.orgs.identity import generate_org_identity, load_cert_info
from apple_device_cli.enrollment.skip_panes import resolve_skip_panes
from apple_device_cli.enrollment.supervised import make_supervised
from apple_device_cli.enrollment.activation import activate_device
from apple_device_cli.core.exceptions import AppleDeviceError

app = typer.Typer(help="iOS device supervised enrollment CLI")
device_app = typer.Typer(help="Device management commands")
org_app = typer.Typer(help="Organization management commands")
enroll_app = typer.Typer(help="Enrollment commands")

app.add_typer(device_app, name="device")
app.add_typer(org_app, name="org")
app.add_typer(enroll_app, name="enroll")

@app.callback(invoke_without_command=True)
def cli_main(ctx: typer.Context):
    """Apple Configurator-like CLI for Linux.

    Manage iOS device enrollment with supervised pairing.
    """
    if ctx.invoked_subcommand is None:
        typer.secho("ios-enroll - iOS device supervised enrollment CLI\n", fg=typer.colors.BLUE, bold=True)
        typer.echo("Manage iOS device enrollment with supervised pairing.\n")
        typer.echo("Commands:")
        typer.echo("  ios-enroll enroll guided-enroll  Guided interactive enrollment")
        typer.echo("  ios-enroll device list           List connected devices")
        typer.echo("  ios-enroll org list             List organizations")
        typer.echo("  ios-enroll --help               Show all commands")
        typer.echo("\nExamples:")
        typer.echo("  ios-enroll enroll guided-enroll  Start guided enrollment")
        typer.echo("  ios-enroll device list          Show connected devices")
        typer.echo("  ios-enroll org create --name 'My Org'  Create organization")


@enroll_app.command("guided-enroll")
def enroll_guided_enroll():
    """Guided supervised enrollment workflow matching Apple Configurator.

    Steps:
    1. Select device
    2. Choose MDM server configuration
    3. Configure organization & supervision identity
    4. Select Setup Assistant skip panes
    5. Erase device if needed
    6. Apply configuration

    This mimics Apple Configurator's Prepare Assistant workflow.
    """
    interactive_enroll()


def interactive_enroll():
    """Guided enrollment workflow matching Apple Configurator's Prepare Assistant.
    
    Steps:
    1. Select device
    2. Choose MDM server (new or existing)
    3. Configure organization & supervision identity
    4. Select skip panes
    5. Erase if needed
    6. Apply configuration
    """
    from apple_device_cli.enrollment.skip_panes import PRESETS
    from apple_device_cli.orgs.identity import generate_org_identity

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
        typer.echo(f"  [{i + 1}] {d.udid}  ({d.device_name})")
    typer.echo()
    choice = typer.prompt("Select device number", default="1")
    try:
        selected = devices[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        typer.secho("Invalid selection", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"\nSelected: {selected.device_name} ({selected.udid})")

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
                typer.echo(f"  [{i + 1}] {o.name} ({o.mdm_url})")
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
        typer.echo(f"\nMDM Server URL: {mdm_url}")
        if checkin_url:
            typer.echo(f"Check-in URL: {checkin_url}")

    # Step 3: Organization Configuration
    typer.echo("\nStep 3: Organization & Supervision Identity")
    typer.secho("-" * 40)
    manager = OrganizationManager()
    orgs = manager.list_orgs()
    existing_orgs = [o for o in orgs if o.cert_path and o.key_path]

    typer.echo("Organization options:")
    typer.echo("  [n] Create new organization")
    if existing_orgs:
        for i, o in enumerate(existing_orgs):
            typer.echo(f"  [{i + 1}] {o.name}")
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
        typer.secho(f"Organization '{name}' saved.", fg=typer.colors.GREEN)
    else:
        try:
            org = existing_orgs[int(org_choice) - 1]
            if mdm_url and (org.mdm_url != mdm_url or org.checkin_url != checkin_url or org.mdm_topic != mdm_topic):
                org.mdm_url = mdm_url
                org.checkin_url = checkin_url
                org.mdm_topic = mdm_topic
                manager.save_org(org, overwrite=True)
                typer.secho(f"Updated MDM settings for '{org.name}'", fg=typer.colors.GREEN)
        except (ValueError, IndexError) as exc:
            typer.secho("Invalid selection", fg=typer.colors.RED)
            raise typer.Exit(1) from exc

    if not org or not org.cert_path or not org.key_path:
        typer.secho(f"\nOrganization '{org.name if org else 'unknown'}' missing cert/key.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"\nOrganization: {org.name}")
    typer.echo(f"MDM URL: {org.mdm_url or 'Not set'}")

    # Step 4: Skip panes selection
    typer.echo("\nStep 4: Setup Assistant Skip Panes")
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

    # Step 5: Erase if needed
    typer.echo("\nStep 5: Device Preparation")
    typer.secho("-" * 40)

    # Get full device state for smart erase decision
    try:
        lockdown = asyncio.run(_get_device_activation_state(selected.udid))
        is_supervised = lockdown.get("IsSupervised", False)
        has_cloud_config = lockdown.get("CloudConfigurationWasApplied", False)
    except Exception:
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
            raise typer.Exit()
    elif is_supervised:
        # Supervised without cloud config is impossible state (shouldn't happen)
        typer.secho("Device is supervised but has no cloud config (unexpected state).", fg=typer.colors.YELLOW)
        needs_erase = True
        if not typer.confirm("Erase device to reset to clean state?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW)
            raise typer.Exit()
    elif activation_state == "Activated":
        # Activated but clean (no cloud config, not supervised) - no erase needed
        typer.secho("Device is activated and clean. Ready for supervision.", fg=typer.colors.GREEN)
        needs_erase = False
    else:
        # Unactivated and clean - no erase needed
        typer.secho("Fresh device detected (unactivated, clean). Applying configuration directly.", fg=typer.colors.GREEN)
        needs_erase = False

    if needs_erase:
        typer.echo("Erasing device...")
        if not selected.ecid:
            typer.secho("Cannot erase: device ECID not available. Connect device in normal mode first.", fg=typer.colors.RED)
            raise typer.Exit(1)
        try:
            firmware_url = resolve_firmware_url(selected.device_type, "latest")
            typer.echo(f"Using latest signed iOS for {selected.device_type}...")
        except Exception as e:
            typer.secho(f"Failed to resolve IPSW: {e}", fg=typer.colors.RED)
            typer.echo("Provide --ipsw or --version to override, or ensure device is connected in normal mode.")
            raise typer.Exit(1)
        try:
            erase_device(selected.udid, selected.ecid, ipsw=firmware_url)
        except Exception as e:
            typer.secho(f"Erase failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)
        typer.secho("Device erased. Waiting 60s for boot...", fg=typer.colors.YELLOW)
        time.sleep(60)
        typer.secho("Device ready for supervised pairing.", fg=typer.colors.GREEN)

    # Step 6: Apply configuration
    typer.echo("\nStep 6: Apply Configuration")
    typer.secho("-" * 40)
    typer.echo("Enrolling device as supervised...")

    # Progress callback for enrollment steps
    def progress_callback(msg: str) -> None:
        typer.echo(f"  {msg}")

    try:
        result = make_supervised(
            cert_path=org.cert_path,
            key_path=org.key_path,
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            udid=selected.udid,
            progress_callback=progress_callback,
        )
        typer.secho("\n" + "=" * 50, fg=typer.colors.GREEN, bold=True)
        if result.success:
            typer.secho("  Device is now supervised and enrolled!", fg=typer.colors.GREEN, bold=True)
        else:
            typer.secho("  Enrollment completed with errors", fg=typer.colors.YELLOW, bold=True)
        typer.secho("=" * 50, fg=typer.colors.GREEN, bold=True)
        typer.echo(f"\n  Organization: {org.name}")
        typer.echo(f"  Device UDID: {result.device_udid}")
        typer.echo(f"  Supervised: {result.supervised}")
        typer.echo(f"  MDM Enrolled: {result.mdm_enrolled}")
        if org.mdm_url:
            typer.echo(f"  MDM Server URL: {org.mdm_url}")
        if result.cloud_config and result.cloud_config.get("MDMServerURL"):
            typer.echo(f"  Cloud Config MDM URL: {result.cloud_config['MDMServerURL']}")
        typer.echo(f"  Skip panes: {len(skip_list)} configured")
        if not result.mdm_enrolled and org.mdm_url:
            typer.secho("\n  NOTE: Device will enroll with MDM on first boot via Setup Assistant.", fg=typer.colors.CYAN)
            typer.echo("  Connect device to power and let it complete Setup Assistant.")
        if result.errors:
            typer.secho("\n  Errors:", fg=typer.colors.YELLOW)
            for error in result.errors:
                typer.echo(f"    - {error}")
        typer.echo("\n  Connect device to power and wait for Setup Assistant...")
    except AppleDeviceError as e:
        typer.secho(f"Enrollment failed: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)


def _prompt_for_udid(udid: str | None, allow_empty: bool = False) -> DeviceInfo | None:
    """Resolve a device selection, prompting the user when needed."""
    if udid:
        # For explicit UDID, try IRecv first since the device is likely in
        # Recovery mode (usbmuxd not serving Recovery devices). Use udid as
        # serial to match, but IRecv.connect() will use the ECID from the
        # connected device.
        from apple_device_cli.device.connection import get_device_info_for_recovery

        info = get_device_info_for_recovery(udid)
        if info and info.device_type not in ("", "Unknown"):
            return info
        # Also try normal-mode lockdown in case device is in normal mode
        info = get_device_info(udid)
        if info and info.device_type not in ("", "Unknown"):
            return info
        # Last resort: return stub (caller can still proceed with --ipsw)
        return DeviceInfo(
            udid=udid,
            device_name="Unknown",
            device_type="Unknown",
            build_version="Unknown",
            firmware_version="Unknown",
        )

    # Start with normal-mode devices
    devices = list_devices()
    recovery_devices: list[DeviceInfo] = []

    # Also check for Recovery/DFU mode devices
    try:
        from apple_device_cli.restore.erase import get_irecv
        irecv = get_irecv()
        # IRecv connects to Recovery mode device and populates attributes directly
        ecid_val = getattr(irecv, 'ecid', None)
        if ecid_val:
            ecid_str = hex(ecid_val)
            try:
                iboot = irecv.iboot_version
            except Exception:
                iboot = "Unknown"
            recovery_devices.append(DeviceInfo(
                udid=ecid_str,
                device_name=getattr(irecv, 'display_name', None) or "Recovery Mode Device",
                device_type=getattr(irecv, 'product_type', None) or "Unknown",
                build_version=iboot,
                firmware_version="",
                ecid=ecid_str,
            ))
    except Exception:
        pass

    # Combine normal and recovery devices for selection
    all_devices = devices + recovery_devices

    if not all_devices:
        if allow_empty:
            return None
        typer.secho("No devices found. Connect a device and try again.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo("Available devices:")
    for i, device in enumerate(all_devices, start=1):
        extra = f" [ECID: {device.ecid}]" if device.ecid else ""
        mode_note = " [Recovery]" if device.udid.startswith("0x") else ""
        typer.echo(f"  [{i}] {device.udid}  ({device.device_name}){extra}{mode_note}")
    typer.echo()
    choice = typer.prompt("Select device number", default="1")
    try:
        return all_devices[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        typer.secho("Invalid selection", fg=typer.colors.RED)
        raise typer.Exit(1) from exc


def _device_is_in_recovery_mode(ecid: str | None) -> bool:
    """Check if a device with the given ECID is currently in Recovery mode."""
    if not ecid:
        return False
    try:
        from apple_device_cli.restore.erase import get_irecv
        IRecv = get_irecv.__globals__.get("IRecv")
        if IRecv is None:
            from pymobiledevice3.irecv import IRecv as _IRecv
        else:
            _IRecv = IRecv
        irecv = _IRecv(ecid=int(ecid, 16), timeout=2, is_recovery=True)
        return irecv._device is not None
    except Exception:
        return False


def _prompt_for_signed_firmware(device: DeviceInfo) -> str:
    """Prompt the user to choose a signed IPSW URL for a device type."""
    firmwares = get_signed_firmwares(device.device_type)
    typer.echo("Signed iOS versions:")
    for i, firmware in enumerate(firmwares, start=1):
        typer.echo(f"  [{i}] {firmware['version']} ({firmware['buildid']})")
    typer.echo()
    choice = typer.prompt("Select iOS version", default="1")
    try:
        selected = firmwares[int(choice) - 1]
    except (ValueError, IndexError) as exc:
        typer.secho("Invalid version selection", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.secho(
        f"Using signed build {selected['version']} ({selected['buildid']})",
        fg=typer.colors.GREEN,
    )
    return selected["url"]


async def _get_device_activation_state(udid: str):
    from pymobiledevice3.lockdown import create_using_usbmux

    lockdown = await create_using_usbmux(serial=udid)
    return lockdown.all_values


def _prompt_for_work_dir(needed_mb: int, available_mb: int, error_msg: str) -> Path | None:
    """Prompt user for a work directory with enough space.

    Args:
        needed_mb: Minimum space needed in MB
        available_mb: Current available space in MB
        error_msg: The original error message

    Returns:
        Path to selected directory, or None if cancelled
    """
    typer.secho(f"\n{error_msg}", fg=typer.colors.RED)
    typer.echo(f"Need {needed_mb} MB but only {available_mb} MB available.")
    typer.echo()

    choice = typer.prompt(
        "Choose an action: [1] Specify different directory, [2] Use current anyway, [3] Cancel",
        default="1"
    )

    if choice == "2":
        typer.secho("Proceeding without guaranteed space (may fail)...", fg=typer.colors.YELLOW)
        return None
    elif choice == "3":
        typer.secho("Cancelled.", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    else:
        new_dir = typer.prompt("Enter path to directory with more space (e.g. /mnt/drive)")
        return Path(new_dir)


def _create_org_interactive(manager: OrganizationManager) -> Organization:
    name = typer.prompt("Organization name")
    org_id = typer.prompt("Organization ID (e.g. com.example)", default="")
    mdm_url = typer.prompt("MDM Server URL (leave blank to skip)", default="")

    generate = typer.confirm("Generate a new supervising identity now?")
    if generate:
        valid_days = typer.prompt("Certificate validity (days)", default=str(365 * 5))
        cert_der, key_der = generate_org_identity(name, int(valid_days))
        org_dir = manager.orgs_dir / manager._sanitize_name(name)
        if org_dir.exists():
            typer.secho(f"Overwriting existing organization directory '{org_dir}'.", fg=typer.colors.YELLOW)
            shutil.rmtree(org_dir)
        org_dir.mkdir(parents=True, exist_ok=True)
        with open(org_dir / "cert.der", "wb") as f:
            f.write(cert_der)
        with open(org_dir / "key.der", "wb") as f:
            f.write(key_der)
        org = Organization(
            name=name,
            org_id=org_id or None,
            mdm_url=mdm_url or None,
            cert_path=str(org_dir / "cert.der"),
            key_path=str(org_dir / "key.der"),
        )
    else:
        cert_path = typer.prompt("Path to certificate (DER)", default="")
        key_path = typer.prompt("Path to private key (DER)", default="")
        org = Organization(
            name=name,
            org_id=org_id or None,
            mdm_url=mdm_url or None,
            cert_path=cert_path or None,
            key_path=key_path or None,
        )

    manager.save_org(org)
    typer.secho(f"Organization '{name}' saved.", fg=typer.colors.GREEN)
    return org


@app.command()
def version():
    """Show version."""
    typer.echo(f"ios-enroll {__version__}")


@device_app.command("list")
def device_list():
    """List connected devices."""
    try:
        devices = list_devices()
        if not devices:
            typer.secho("No devices found", fg=typer.colors.YELLOW)
            return
        for d in devices:
            typer.echo(f"{d.udid}\t{d.device_name}")
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("info")
def device_info(udid: str = typer.Option(None, "--udid")):
    """Get device info."""
    if not udid:
        devices = list_devices()
        if not devices:
            typer.secho("No device found", fg=typer.colors.RED)
            return
        udid = devices[0].udid
    info = get_device_info(udid)
    if info:
        typer.echo(f"UDID: {info.udid}")
        typer.echo(f"Name: {info.device_name}")
        typer.echo(f"Type: {info.device_type}")
        typer.echo(f"iOS: {info.firmware_version} ({info.build_version})")
        if info.ecid:
            typer.echo(f"ECID: {info.ecid}")
    else:
        typer.secho(f"Device not found: {udid}", fg=typer.colors.RED)


@device_app.command("erase")
def device_erase(
    udid: str = typer.Option(None, "--udid"),
    ipsw: str = typer.Option(None, "--ipsw"),
    ios_version: str = typer.Option(None, "--version"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
    enter_recovery: bool = typer.Option(True, "--enter-recovery/--no-enter-recovery"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
    work_dir: str = typer.Option(None, "--work-dir", help="Directory to store IPSW file (default: current directory)"),
):
    """Erase all data on a device and restore to factory state.

    Requires an IPSW file (provide --ipsw or --version)."""
    device = _prompt_for_udid(udid)
    if device is None:
        typer.secho("No device selected", fg=typer.colors.RED)
        return
    if ipsw and ios_version:
        typer.secho("Use either --ipsw or --version, not both.", fg=typer.colors.RED)
        raise typer.Exit(1)

    # --- Choose firmware ---
    try:
        if ipsw:
            selected_ipsw = ipsw
        elif ios_version:
            if device.device_type in ("", "Unknown"):
                typer.secho(
                    "Cannot resolve --version without device type. Provide --ipsw URL/path.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            typer.secho(f"Resolving signed build for iOS {ios_version}...", fg=typer.colors.YELLOW)
            selected_ipsw = resolve_firmware_url(device.device_type, ios_version)
            typer.secho(f"Target iOS: {ios_version}  ->  {selected_ipsw}", fg=typer.colors.GREEN)
        elif yes:
            if device.device_type in ("", "Unknown"):
                typer.secho(
                    "Cannot auto-select firmware without device type. Provide --ipsw URL/path or --version.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            firmwares = get_signed_firmwares(device.device_type)
            latest = firmwares[0]
            selected_ipsw = latest["url"]
            typer.secho(
                f"Auto-selected latest signed build: {latest['version']} ({latest['buildid']})",
                fg=typer.colors.GREEN,
            )
        else:
            selected_ipsw = _prompt_for_signed_firmware(device)

        # --- Preflight info ---
        typer.echo()
        typer.secho("=" * 52, fg=typer.colors.RED, bold=True)
        typer.secho("  WARNING: This will ERASE ALL DATA on the device", fg=typer.colors.RED, bold=True)
        typer.secho("=" * 52, fg=typer.colors.RED, bold=True)
        typer.echo()
        typer.echo(f"  Device:  {device.device_name}")
        typer.echo(f"  UDID:    {device.udid}")
        if device.firmware_version:
            typer.echo(f"  iOS:     {device.firmware_version} ({device.build_version})")
        if device.ecid:
            typer.echo(f"  ECID:    {device.ecid}")
        typer.echo()
        typer.secho("  This operation cannot be undone.", fg=typer.colors.YELLOW)
        typer.secho("  Ensure the device is charged and connected before proceeding.", fg=typer.colors.YELLOW)
        typer.echo()

        if not yes and not force:
            typer.confirm("Proceed with erase?", default=True, abort=True)
        if not force:
            confirm_name = typer.prompt(
                f'  Type the device name "{device.device_name}" to confirm erase'
            )
            if confirm_name.strip() != device.device_name:
                typer.secho("Confirmation did not match. Erase cancelled.", fg=typer.colors.RED)
                raise typer.Exit(1)

        if enter_recovery:
            # Check if device is already in Recovery mode
            if _device_is_in_recovery_mode(device.ecid):
                typer.secho("Device is already in Recovery mode.", fg=typer.colors.GREEN)
            else:
                typer.secho(
                    "Placing device into Recovery mode (waiting up to 3 min)...",
                    fg=typer.colors.YELLOW,
                )
                if not wait_for_udid_in_usbmux(device.udid, timeout=60):
                    raise AppleDeviceError(
                        "Device is not visible in normal mode via usbmux. Unlock the iPad, accept Trust, and reconnect USB."
                    )
                typer.secho("Verifying trust/pairing with device...", fg=typer.colors.YELLOW)
                ensure_device_pairing(device.udid)
                enter_recovery_mode(device.udid, device.ecid or None)
                typer.secho("Device is in Recovery mode.", fg=typer.colors.GREEN)
        elif not _device_is_in_recovery_mode(device.ecid):
            # User said don't enter recovery, but device is in normal mode
            # and we're doing an erase which requires Recovery mode
            raise AppleDeviceError(
                "Cannot erase: device is in normal mode but --no-enter-recovery was set. "
                "Erase requires the device to be in Recovery mode. Omit --no-enter-recovery."
            )

        typer.secho(
            "Starting erase. Restore output will stream live below.",
            fg=typer.colors.YELLOW,
        )

        current_work_dir = work_dir
        while True:
            try:
                erase_device(device.udid, device.ecid or None, ipsw=selected_ipsw, work_dir=current_work_dir)
                break
            except InsufficientSpaceError as e:
                if current_work_dir:
                    typer.secho(f"Not enough space at specified directory: {current_work_dir}", fg=typer.colors.RED)
                new_dir = _prompt_for_work_dir(e.needed_mb, e.available_mb, str(e))
                if new_dir is None:
                    current_work_dir = None
                else:
                    current_work_dir = str(new_dir)

        typer.secho("Erase completed", fg=typer.colors.GREEN)
    except typer.Abort:
        typer.secho("Erase cancelled.", fg=typer.colors.YELLOW)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("update")
def device_update(
    udid: str = typer.Option(None, "--udid"),
    ipsw: str = typer.Option(None, "--ipsw"),
    ios_version: str = typer.Option(None, "--version"),
    enter_recovery: bool = typer.Option(True, "--enter-recovery/--no-enter-recovery"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation prompt"),
    work_dir: str = typer.Option(None, "--work-dir", help="Directory to store IPSW file (default: current directory)"),
):
    """Update device to a signed iOS version (preserves user data)."""
    device = _prompt_for_udid(udid)
    if device is None:
        typer.secho("No device selected", fg=typer.colors.RED)
        return
    if ipsw and ios_version:
        typer.secho("Use either --ipsw or --version, not both.", fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        # --- Preflight info ---
        typer.echo()
        typer.secho("iOS Update / Upgrade", fg=typer.colors.BLUE, bold=True)
        typer.secho("-" * 40)
        typer.echo(f"  Device:  {device.device_name}")
        typer.echo(f"  UDID:    {device.udid}")
        if device.firmware_version:
            typer.echo(f"  Current iOS:  {device.firmware_version} ({device.build_version})")
        if device.ecid:
            typer.echo(f"  ECID:    {device.ecid}")
        typer.echo()

        # --- Choose firmware ---
        if ipsw:
            selected_ipsw = ipsw
            typer.secho(f"Target IPSW: {selected_ipsw}", fg=typer.colors.GREEN)
        elif ios_version:
            if device.device_type in ("", "Unknown"):
                typer.secho(
                    "Cannot resolve --version without device type. Provide --ipsw URL/path.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            typer.secho(f"Resolving signed build for iOS {ios_version}...", fg=typer.colors.YELLOW)
            selected_ipsw = resolve_firmware_url(device.device_type, ios_version)
            typer.secho(f"Target iOS: {ios_version}  →  {selected_ipsw}", fg=typer.colors.GREEN)
        elif yes:
            if device.device_type in ("", "Unknown"):
                typer.secho(
                    "Cannot auto-select firmware without device type. Provide --ipsw URL/path or --version.",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
            # Non-interactive mode: auto-select the latest signed build.
            firmwares = get_signed_firmwares(device.device_type)
            latest = firmwares[0]
            selected_ipsw = latest["url"]
            typer.secho(
                f"Auto-selected latest signed build: {latest['version']} ({latest['buildid']})",
                fg=typer.colors.GREEN,
            )
        else:
            selected_ipsw = _prompt_for_signed_firmware(device)

        typer.echo()
        typer.secho("Pre-update checklist:", fg=typer.colors.YELLOW)
        typer.echo("  • Keep the device plugged in throughout the update")
        typer.echo("  • The device will reboot one or more times")
        typer.echo("  • User data is preserved (this is NOT a factory reset)")
        typer.echo()

        if not yes:
            typer.confirm("Proceed with update?", default=True, abort=True)

        if enter_recovery:
            typer.secho(
                "\nStep 1/3  Placing device into Recovery mode (waiting up to 3 min)...",
                fg=typer.colors.YELLOW,
            )
            if not wait_for_udid_in_usbmux(device.udid, timeout=60):
                raise AppleDeviceError(
                    "Device is not visible in normal mode via usbmux. Unlock the iPad, accept Trust, and reconnect USB."
                )
            typer.secho("          Verifying trust/pairing with device...", fg=typer.colors.YELLOW)
            ensure_device_pairing(device.udid)
            enter_recovery_mode(device.udid, device.ecid or None)
            typer.secho("          Device is in Recovery mode.", fg=typer.colors.GREEN)
        typer.secho(
            "\nStep 2/3  Starting update — output streams live below.",
            fg=typer.colors.YELLOW,
        )

        current_work_dir = work_dir
        while True:
            try:
                update_device(device.udid, device.ecid or None, ipsw=selected_ipsw, work_dir=current_work_dir)
                break
            except InsufficientSpaceError as e:
                if current_work_dir:
                    typer.secho(f"Not enough space at specified directory: {current_work_dir}", fg=typer.colors.RED)
                new_dir = _prompt_for_work_dir(e.needed_mb, e.available_mb, str(e))
                if new_dir is None:
                    current_work_dir = None
                else:
                    current_work_dir = str(new_dir)

        typer.echo()
        typer.secho("Step 3/3  Update complete.", fg=typer.colors.GREEN, bold=True)
        typer.echo("          The device will reboot into the updated iOS version.")
    except typer.Abort:
        typer.secho("Update cancelled.", fg=typer.colors.YELLOW)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("restore")
def device_restore(
    udid: str = typer.Option(None, "--udid"),
    ipsw: str = typer.Option(..., "--ipsw"),
    enter_recovery: bool = typer.Option(True, "--enter-recovery/--no-enter-recovery"),
    work_dir: str = typer.Option(None, "--work-dir", help="Directory to store IPSW file (default: current directory)"),
):
    """Restore device with IPSW."""
    device = _prompt_for_udid(udid)
    if device is None:
        typer.secho("No device selected", fg=typer.colors.RED)
        return
    try:
        typer.echo(f"Selected device: {device.device_name} ({device.udid})")
        if enter_recovery:
            typer.secho(
                "Placing device into Recovery mode before restore...",
                fg=typer.colors.YELLOW,
            )
            if not wait_for_udid_in_usbmux(device.udid, timeout=60):
                raise AppleDeviceError(
                    "Device is not visible in normal mode via usbmux. Unlock the iPad, accept Trust, and reconnect USB."
                )
            typer.secho("Verifying trust/pairing with device...", fg=typer.colors.YELLOW)
            ensure_device_pairing(device.udid)
            enter_recovery_mode(device.udid, device.ecid or None)
        typer.secho(
            "Starting restore. pymobiledevice3 output will stream live; the device may reboot or enter Recovery mode.",
            fg=typer.colors.YELLOW,
        )

        current_work_dir = work_dir
        while True:
            try:
                restore_device(device.udid, ipsw, device.ecid or None, work_dir=current_work_dir)
                break
            except InsufficientSpaceError as e:
                if current_work_dir:
                    typer.secho(f"Not enough space at specified directory: {current_work_dir}", fg=typer.colors.RED)
                new_dir = _prompt_for_work_dir(e.needed_mb, e.available_mb, str(e))
                if new_dir is None:
                    current_work_dir = None
                else:
                    current_work_dir = str(new_dir)

        typer.secho("Restore completed", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("enter-recovery")
def device_enter_recovery(
    udid: str = typer.Option(None, "--udid"),
):
    """Place a device into Recovery mode.

    Use this when the device is in Normal mode and you need to perform
    a restore, erase, or update operation.
    """
    device = _prompt_for_udid(udid)
    if device is None:
        typer.secho("No device selected", fg=typer.colors.RED)
        return

    typer.echo(f"Device: {device.device_name} ({device.udid})")
    if device.ecid:
        typer.echo(f"ECID: {device.ecid}")
    typer.echo()

    try:
        # Ensure device is in normal mode and paired
        if not wait_for_udid_in_usbmux(device.udid, timeout=60):
            raise AppleDeviceError(
                "Device is not visible in normal mode via usbmux. "
                "Unlock the device, accept Trust, and reconnect USB."
            )
        typer.secho("Verifying trust/pairing with device...", fg=typer.colors.YELLOW)
        ensure_device_pairing(device.udid)

        typer.secho(
            "Placing device into Recovery mode (waiting up to 3 min)...",
            fg=typer.colors.YELLOW,
        )
        enter_recovery_mode(device.udid, device.ecid or None)
        typer.secho("Device is in Recovery mode.", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@org_app.command("list")
def org_list():
    """List organizations."""
    manager = OrganizationManager()
    orgs = manager.list_orgs()
    if not orgs:
        typer.echo("No organizations stored.")
        typer.echo(f"  Location: {manager.orgs_dir}")
        return
    typer.echo(f"Organizations in: {manager.orgs_dir}")
    for org in orgs:
        typer.echo(f"  {org.name}")
        if org.org_id:
            typer.echo(f"    ID: {org.org_id}")


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
):
    """Create organization with MDM server configuration.

    Example:
        ios-enroll org create --name "My Org" --mdm-url https://mdm.example.com/mdm \\
            --checkin-url https://mdm.example.com/checkin --mdm-topic com.example.mdm
    """
    org = Organization(
        name=name,
        org_id=org_id,
        address=address,
        phone=phone,
        email=email,
        mdm_url=mdm_url,
        checkin_url=checkin_url,
        mdm_topic=mdm_topic,
        mdm_description=mdm_description,
    )
    if cert:
        org.cert_path = str(Path(cert).resolve()) if cert else None
    if key:
        org.key_path = str(Path(key).resolve()) if key else None
    manager = OrganizationManager()
    manager.save_org(org)
    typer.secho(f"Created organization: {org.name}", fg=typer.colors.GREEN)
    if mdm_url:
        typer.echo(f"  MDM URL: {mdm_url}")
    if checkin_url:
        typer.echo(f"  Check-in URL: {checkin_url}")
    if mdm_topic:
        typer.echo(f"  MDM Topic: {mdm_topic}")


@org_app.command("delete")
def org_delete(name: str = typer.Option(..., "--name")):
    """Delete organization."""
    manager = OrganizationManager()
    if manager.delete_org(name):
        typer.secho(f"Deleted organization: {name}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)


@org_app.command("set-cert")
def org_set_cert(name: str = typer.Option(..., "--name"), cert: str = typer.Option(..., "-C", "--cert")):
    """Set certificate for organization."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        return
    org.cert_path = str(Path(cert).resolve())
    manager.save_org(org, overwrite=True)
    typer.secho(f"Set certificate for '{name}'", fg=typer.colors.GREEN)


@org_app.command("set-key")
def org_set_key(name: str = typer.Option(..., "--name"), key: str = typer.Option(..., "-K", "--key")):
    """Set private key for organization."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        return
    org.key_path = str(Path(key).resolve())
    manager.save_org(org, overwrite=True)
    typer.secho(f"Set private key for '{name}'", fg=typer.colors.GREEN)


@org_app.command("set-mdm-url")
def org_set_mdm_url(name: str = typer.Option(..., "--name"), mdm_url: str = typer.Option(..., "--mdm-url")):
    """Set MDM server URL for organization."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        return
    org.mdm_url = mdm_url
    manager.save_org(org, overwrite=True)
    typer.secho(f"Set MDM URL for '{name}'", fg=typer.colors.GREEN)


@org_app.command("set-checkin-url")
def org_set_checkin_url(name: str = typer.Option(..., "--name"), checkin_url: str = typer.Option(..., "--checkin-url")):
    """Set SCEP check-in URL for organization."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        return
    org.checkin_url = checkin_url
    manager.save_org(org, overwrite=True)
    typer.secho(f"Set check-in URL for '{name}'", fg=typer.colors.GREEN)


@org_app.command("set-mdm-topic")
def org_set_mdm_topic(name: str = typer.Option(..., "--name"), mdm_topic: str = typer.Option(..., "--mdm-topic")):
    """Set MDM topic for organization."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        return
    org.mdm_topic = mdm_topic
    manager.save_org(org, overwrite=True)
    typer.secho(f"Set MDM topic for '{name}'", fg=typer.colors.GREEN)


@org_app.command("show")
def org_show(name: str = typer.Option(..., "--name")):
    """Show organization details."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        return
    typer.echo(f"Name: {org.name}")
    if org.org_id:
        typer.echo(f"ID: {org.org_id}")
    if org.address:
        typer.echo(f"Address: {org.address}")
    if org.phone:
        typer.echo(f"Phone: {org.phone}")
    if org.email:
        typer.echo(f"Email: {org.email}")
    if org.mdm_url:
        typer.echo(f"MDM URL: {org.mdm_url}")
    if org.checkin_url:
        typer.echo(f"Check-in URL: {org.checkin_url}")
    if org.mdm_topic:
        typer.echo(f"MDM Topic: {org.mdm_topic}")
    if org.mdm_description:
        typer.echo(f"MDM Description: {org.mdm_description}")
    typer.echo(f"Created: {org.created_at}")
    typer.echo(f"Cert: {org.cert_path or 'Not set'}")
    typer.echo(f"Key: {org.key_path or 'Not set'}")
    if org.cert_path and Path(org.cert_path).exists():
        try:
            cert_info = load_cert_info(Path(org.cert_path).read_bytes())
            cn = cert_info.get("2.5.4.3", None)
            if cn:
                typer.echo(f"Cert CN: {cn}")
        except Exception:
            pass


@org_app.command("import")
def org_import(
    path: str = typer.Option(..., "--path"),
    password: str = typer.Option("", "-p", "--password"),
):
    """Import organization from Apple Configurator .organization file, directory, or zip."""
    manager = OrganizationManager()
    try:
        org = manager.import_org(path, password or "password")
        typer.secho(f"Imported: {org.name}", fg=typer.colors.GREEN)
        typer.echo(f"  Cert: {'Yes' if org.cert_path else 'No'}")
        typer.echo(f"  Key: {'Yes' if org.key_path else 'No'}")
        if org.org_id:
            typer.echo(f"  ID: {org.org_id}")
    except Exception as e:
        typer.secho(f"Import failed: {e}", fg=typer.colors.RED)


@org_app.command("import-mobileconfig")
def org_import_mobileconfig(
    path: str = typer.Option(..., "--path"),
):
    """Import organization from MDM .mobileconfig file."""
    manager = OrganizationManager()
    try:
        org = manager.import_mobileconfig(path)
        typer.secho(f"Imported: {org.name}", fg=typer.colors.GREEN)
        typer.echo(f"  MDM URL: {org.mdm_url or 'Not set'}")
        typer.echo(f"  Check-in URL: {org.checkin_url or 'Not set'}")
        typer.echo(f"  Cert: {'Yes' if org.cert_path else 'No'}")
        typer.echo(f"  Key: {'Yes' if org.key_path else 'No'}")
    except Exception as e:
        typer.secho(f"Import failed: {e}", fg=typer.colors.RED)


@org_app.command("export")
def org_export(name: str = typer.Option(..., "--name"), path: str = typer.Option(..., "--path")):
    """Export organization to directory or zip."""
    manager = OrganizationManager()
    if manager.export_org(name, path):
        typer.secho(f"Exported '{name}' to {path}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)


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
        ios-enroll org generate --name "My Org" --mdm-url https://mdm.example.com/mdm \\
            --checkin-url https://mdm.example.com/checkin --mdm-topic com.example.mdm
    """
    manager = OrganizationManager()
    existing = manager.get_org(name)
    if existing and existing.cert_path and existing.key_path:
        if not typer.confirm(f"Organization '{name}' already has a cert/key. Overwrite?"):
            return

    cert_der, key_der = generate_org_identity(name, valid_days)

    org_dir = manager.orgs_dir / manager._sanitize_name(name)
    if org_dir.exists():
        shutil.rmtree(org_dir)
    org_dir.mkdir(parents=True, exist_ok=True)

    with open(org_dir / "cert.der", "wb") as f:
        f.write(cert_der)
    with open(org_dir / "key.der", "wb") as f:
        f.write(key_der)

    org = Organization(
        name=name,
        org_id=org_id,
        mdm_url=mdm_url,
        checkin_url=checkin_url,
        mdm_topic=mdm_topic,
        mdm_description=mdm_description,
        cert_path=str(org_dir / "cert.der"),
        key_path=str(org_dir / "key.der"),
    )
    org.save(org_dir, skip_copy=True)

    typer.secho(f"Generated identity for: {name}", fg=typer.colors.GREEN)
    if mdm_url:
        typer.echo(f"  MDM URL: {mdm_url}")
    if checkin_url:
        typer.echo(f"  Check-in URL: {checkin_url}")
    if mdm_topic:
        typer.echo(f"  MDM Topic: {mdm_topic}")


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
        return
    manager = OrganizationManager()
    org = manager.get_org(org_name)
    if not org:
        typer.secho(f"Organization not found: {org_name}", fg=typer.colors.RED)
        return
    if not org.cert_path or not org.key_path:
        typer.secho(f"Organization '{org_name}' missing cert or key", fg=typer.colors.RED)
        return
    try:
        skip_list = resolve_skip_panes(skip_preset, skip)
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        return

    # Set up progress callback if verbose mode
    progress_callback = None
    if verbose:
        def progress_callback(msg: str) -> None:
            typer.echo(f"  {msg}")
        typer.echo(f"Supervised enrollment for: {device.device_name} ({device.udid})")

    try:
        result = make_supervised(
            cert_path=org.cert_path,
            key_path=org.key_path,
            org_name=org.name,
            org_uuid=org.org_id,
            skip_list=skip_list,
            mdm_url=org.mdm_url,
            wifi_ssid=wifi_ssid,
            wifi_password=wifi_password,
            wifi_encryption=wifi_encryption,
            mdm_checkin_url=org.checkin_url,
            mdm_topic=org.mdm_topic,
            mdm_unremovable=mdm_unremovable,
            wifi_config=wifi_config,
            udid=device.udid,
            fail_on_mdm_error=fail_on_mdm_error,
            progress_callback=progress_callback,
        )
        if result.success:
            typer.secho("Device is now supervised", fg=typer.colors.GREEN)
            typer.echo(f"  UDID: {result.device_udid}")
            typer.echo(f"  Supervised: {result.supervised}")
            typer.echo(f"  MDM Enrolled: {result.mdm_enrolled}")
        else:
            typer.secho("Enrollment completed with errors:", fg=typer.colors.YELLOW)
            for error in result.errors:
                typer.echo(f"  - {error}")
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


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
        return

    if not force:
        typer.echo()
        typer.secho("WARNING: This will remove supervised configuration from the device.", fg=typer.colors.YELLOW)
        typer.echo(f"  Device: {device.device_name} ({device.udid})")
        typer.echo()
        confirm = typer.confirm("Continue with re-enrollment preparation?")
        if not confirm:
            typer.secho("Cancelled.", fg=typer.colors.YELLOW)
            return

    try:
        typer.echo("Erasing cloud configuration...")
        erase_device_for_reenrollment(device.udid)
        typer.secho("Device cloud config erased. Ready for fresh enrollment.", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


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

    typer.echo(f"Device: {device.device_name} ({device.udid})")
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
            typer.echo(f"  Organization: {state['org_name']}")
        if state.get('org_magic'):
            typer.echo(f"  Organization ID: {state['org_magic']}")
        typer.echo(f"  MDM Managed: {state.get('is_mdm_managed', False)}")
    except Exception as e:
        typer.secho(f"Error getting device status: {e}", fg=typer.colors.RED)


@enroll_app.command("validate")
def enroll_validate(
    org_name: str = typer.Option(..., "--org-name"),
    mdm_url: str = typer.Option(None, "--mdm-url"),
    check_mdm: bool = typer.Option(False, "--check-mdm", help="Verify MDM server is reachable"),
):
    """Validate enrollment prerequisites without touching devices.

    Checks that the organization exists with valid cert/key and optionally
    verifies the MDM server is reachable.
    """
    from apple_device_cli.enrollment.supervised import validate_enrollment_prerequisites

    manager = OrganizationManager()
    org = manager.get_org(org_name)
    if not org:
        typer.secho(f"Organization not found: {org_name}", fg=typer.colors.RED)
        return

    typer.echo(f"Validating organization: {org_name}")
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
        typer.echo(f"  Certificate: {org.cert_path}")
        typer.echo(f"  Private Key: {org.key_path}")
        if target_mdm_url:
            typer.echo(f"  MDM URL: {target_mdm_url}")
    else:
        typer.secho("Validation failed:", fg=typer.colors.RED)
        for error in errors:
            typer.echo(f"  - {error}")


@enroll_app.command("activate")
def enroll_activate(udid: str = typer.Option(None, "--udid")):
    """Activate device."""
    try:
        activate_device(udid)
        typer.secho("Device activated", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


def main():
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    main()
