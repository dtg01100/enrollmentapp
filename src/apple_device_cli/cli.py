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
    """Guided supervised enrollment workflow.

    This command walks you through:
    1. Selecting a connected device
    2. Choosing or creating an organization
    3. Configuring skip panes
    4. Erasing device if needed
    5. Performing supervised pairing with cloud configuration

    For quick device enrollment with interactive prompts.
    """
    interactive_enroll()


def interactive_enroll():
    from apple_device_cli.enrollment.skip_panes import PRESETS

    typer.secho("=== Apple Device Enrollment ===\n", fg=typer.colors.BLUE, bold=True)

    devices = list_devices()
    if not devices:
        typer.secho("No devices found. Connect a device and try again.", fg=typer.colors.RED)
        raise typer.Exit(1)

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

    manager = OrganizationManager()
    orgs = manager.list_orgs()

    if not orgs:
        typer.secho("\nNo organizations found. Let's create one.", fg=typer.colors.YELLOW)
        org = _create_org_interactive(manager)
    else:
        typer.echo("\nOrganizations:")
        for i, o in enumerate(orgs):
            typer.echo(f"  [{i + 1}] {o.name}" + (f" ({o.org_id})" if o.org_id else ""))
        typer.echo("  [n] Create new organization")
        choice = typer.prompt("Select organization number", default="1")
        if choice.lower() == "n":
            org = _create_org_interactive(manager)
        else:
            try:
                org = orgs[int(choice) - 1]
            except (ValueError, IndexError):
                typer.secho("Invalid selection", fg=typer.colors.RED)
                raise typer.Exit(1)

    if not org.cert_path or not org.key_path:
        typer.secho(f"\nOrganization '{org.name}' has no cert/key.", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"\nOrganization: {org.name}")
    typer.echo(f"MDM URL: {org.mdm_url or 'Not set'}")

    typer.echo("\nSkip panes presets:")
    for name in PRESETS:
        typer.echo(f"  - {name}")
    preset_choice = typer.prompt("Select preset", default="standard")
    skip_list = PRESETS.get(preset_choice, PRESETS["standard"])

    needs_erase = activation_state != "Activated" or has_cloud_config
    if needs_erase:
        typer.secho("\nDevice needs erase before enrollment.", fg=typer.colors.YELLOW)
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
        typer.secho("Ready for supervised pairing.", fg=typer.colors.GREEN)

    typer.echo("\nEnrolling device as supervised...")
    try:
        cloud_config = make_supervised(org.cert_path, org.key_path, org.name, org.org_id, skip_list, org.mdm_url)
        typer.secho("\nDevice is now supervised and enrolled!", fg=typer.colors.GREEN)
        if org.mdm_url:
            typer.echo(f"MDM Server URL: {org.mdm_url}")
        if cloud_config.get("MDMServerURL"):
            typer.echo(f"Cloud config MDM URL: {cloud_config['MDMServerURL']}")
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
    cert: str = typer.Option(None, "-C", "--cert"),
    key: str = typer.Option(None, "-K", "--key"),
):
    """Create organization."""
    org = Organization(name=name, org_id=org_id, address=address, phone=phone, email=email, mdm_url=mdm_url)
    if cert:
        org.cert_path = str(Path(cert).resolve()) if cert else None
    if key:
        org.key_path = str(Path(key).resolve()) if key else None
    manager = OrganizationManager()
    manager.save_org(org)
    typer.secho(f"Created organization: {org.name}", fg=typer.colors.GREEN)


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
    manager.save_org(org)
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
    manager.save_org(org)
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
    manager.save_org(org)
    typer.secho(f"Set MDM URL for '{name}'", fg=typer.colors.GREEN)


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
    valid_days: int = typer.Option(365 * 5, "--valid-days"),
):
    """Generate a new supervising identity for an organization."""
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
        cert_path=str(org_dir / "cert.der"),
        key_path=str(org_dir / "key.der"),
    )
    org.save(org_dir, skip_copy=True)

    typer.secho(f"Generated identity for: {name}", fg=typer.colors.GREEN)


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
