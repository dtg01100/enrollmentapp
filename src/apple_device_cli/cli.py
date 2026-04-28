from pathlib import Path
import shutil

import asyncio
import typer

from apple_device_cli import __version__
from apple_device_cli.device.connection import list_devices, get_device_info
from apple_device_cli.restore.erase import erase_device, update_device, restore_device
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
    except (ValueError, IndexError):
        typer.secho("Invalid selection", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"\nSelected: {selected.device_name} ({selected.udid})")

    # Get device state
    try:
        lockdown = asyncio.get_event_loop().run_until_complete(
            asyncio.to_thread(_get_device_activation_state, selected.udid)
        )
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
            except (ValueError, IndexError):
                typer.secho("Invalid selection", fg=typer.colors.RED)
                raise typer.Exit(1)

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
        # Create new organization
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
            # Update MDM settings if new server was configured
            if mdm_url and (org.mdm_url != mdm_url or org.checkin_url != checkin_url or org.mdm_topic != mdm_topic):
                org.mdm_url = mdm_url
                org.checkin_url = checkin_url
                org.mdm_topic = mdm_topic
                manager.save_org(org, overwrite=True)
                typer.secho(f"Updated MDM settings for '{org.name}'", fg=typer.colors.GREEN)
        except (ValueError, IndexError):
            typer.secho("Invalid selection", fg=typer.colors.RED)
            raise typer.Exit(1)

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
    needs_erase = activation_state != "Activated" or has_cloud_config
    
    if needs_erase:
        typer.secho(f"Device state requires erase (activation: {activation_state}, cloud config: {has_cloud_config}).", fg=typer.colors.YELLOW)
        if not typer.confirm("Erase device now?"):
            typer.secho("Aborted.", fg=typer.colors.YELLOW)
            raise typer.Exit()
        typer.echo("Erasing device...")
        try:
            erase_device(selected.udid)
        except AppleDeviceError as e:
            typer.secho(f"Erase failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(1)
        typer.secho("Device erased. Waiting 60s for boot...", fg=typer.colors.YELLOW)
        import time
        time.sleep(60)
        typer.secho("Device ready for supervised pairing.", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Device activation: {activation_state}")
        typer.echo("No erase required.")

    # Step 6: Apply configuration
    typer.echo("\nStep 6: Apply Configuration")
    typer.secho("-" * 40)
    typer.echo("Enrolling device as supervised...")
    try:
        cloud_config = make_supervised(
            org.cert_path, org.key_path, org.name, org.org_id, 
            skip_list, org.mdm_url
        )
        typer.secho("\n" + "=" * 50, fg=typer.colors.GREEN, bold=True)
        typer.secho("  Device is now supervised and enrolled!", fg=typer.colors.GREEN, bold=True)
        typer.secho("=" * 50, fg=typer.colors.GREEN, bold=True)
        typer.echo(f"\n  Organization: {org.name}")
        if org.mdm_url:
            typer.echo(f"  MDM Server URL: {org.mdm_url}")
        if cloud_config.get("MDMServerURL"):
            typer.echo(f"  Cloud Config MDM URL: {cloud_config['MDMServerURL']}")
        typer.echo(f"  Skip panes: {len(skip_list)} configured")
        typer.echo("\n  Connect device to power and wait for Setup Assistant...")
    except AppleDeviceError as e:
        typer.secho(f"Enrollment failed: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)


def _get_device_activation_state(udid: str) -> dict:
    from pymobiledevice3.lockdown import create_using_usbmux

    async def _get():
        lockdown = await create_using_usbmux()
        return lockdown.all_values

    return asyncio.run(_get())


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
    else:
        typer.secho(f"Device not found: {udid}", fg=typer.colors.RED)


@device_app.command("erase")
def device_erase(udid: str = typer.Option(..., "--udid")):
    """Erase device."""
    try:
        erase_device(udid)
        typer.secho("Erase completed", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("update")
def device_update(udid: str = typer.Option(..., "--udid")):
    """Update device to latest iOS."""
    try:
        update_device(udid)
        typer.secho("Update completed", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


@device_app.command("restore")
def device_restore(udid: str = typer.Option(..., "--udid"), ipsw: str = typer.Option(..., "--ipsw")):
    """Restore device with IPSW."""
    try:
        restore_device(udid, ipsw)
        typer.secho("Restore completed", fg=typer.colors.GREEN)
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
    udid: str = typer.Option(..., "--udid"),
    org_name: str = typer.Option(..., "--org-name"),
    skip_preset: str = typer.Option(None, "--skip-preset"),
    skip: list[str] = typer.Option([], "--skip"),
    wifi_ssid: str = typer.Option(None, "--wifi-ssid"),
    wifi_password: str = typer.Option(None, "--wifi-password"),
    wifi_encryption: str = typer.Option("WPA", "--wifi-encryption"),
):
    """Make device supervised."""
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

    try:
        make_supervised(org.cert_path, org.key_path, org.name, org.org_id, skip_list, org.mdm_url, wifi_ssid, wifi_password, wifi_encryption)
        typer.secho("Device is now supervised", fg=typer.colors.GREEN)
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)


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
