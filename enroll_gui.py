#!/usr/bin/env python3
"""
iOS Enrollment Tool - Gradio Web GUI with Organization Management
"""

import subprocess
import sys
import json
from pathlib import Path
import gradio as gr

ENROLL_SCRIPT = Path(__file__).parent / "enroll.py"
ORGS_DIR = Path.home() / ".config" / "enrollment" / "orgs"


def run_enroll(args):
    cmd = [sys.executable, str(ENROLL_SCRIPT)] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout if result.stdout else ""
        if result.stderr:
            output += f"\n{result.stderr}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {e}"


def get_orgs():
    result = run_enroll(["org", "list", "--json"])
    try:
        orgs = json.loads(result)
        return [o.get("name", "") for o in orgs] if orgs else []
    except:
        return []


def get_org_details(name):
    if not name:
        return ""
    result = run_enroll(["org", "list", "--json"])
    try:
        orgs = json.loads(result)
        for org in orgs:
            if org.get("name") == name:
                return json.dumps(org, indent=2)
        return f"Organization '{name}' not found"
    except:
        return result


def get_devices():
    result = run_enroll(["list", "--json"])
    try:
        devs = json.loads(result)
        return [d.get("UDID", "") for d in devs] if devs else []
    except:
        return []


def erase(udid, skip_esim):
    if not udid:
        return "Error: Select a device"
    args = ["erase", "--udid", udid]
    if skip_esim:
        args.append("--skip-esim")
    return run_enroll(args)


def update(udid, skip_completed):
    if not udid:
        return "Error: Select a device"
    args = ["update", "--udid", udid]
    if skip_completed:
        args.append("--skip-update-completed")
    return run_enroll(args)


def make_supervised(udid, org_name):
    if not udid or not org_name:
        return "Error: Select both device and organization"
    return run_enroll(["make-supervised", "--udid", udid, "--org-name", org_name])


def restore(udid, ipsw_path, skip_completed):
    if not udid:
        return "Error: Select a device"
    if not ipsw_path:
        return "Error: Select an IPSW file"
    args = ["restore", "--udid", udid, "--ipsw", ipsw_path]
    if skip_completed:
        args.append("--skip-restore-completed")
    return run_enroll(args)


def get_info(udid):
    if not udid:
        return "Error: Select a device"
    return run_enroll(["info", "--udid", udid, "--json"])


def full_enroll(udid, org_name):
    if not udid or not org_name:
        return "Error: Select both device and organization"
    return run_enroll(["enroll", "--udid", udid, "--org-name", org_name])


def main():
    orgs = get_orgs()
    devices = get_devices()

    with gr.Blocks(title="iOS Enrollment Tool") as app:
        gr.Markdown("# iOS Enrollment Tool")
        gr.Markdown("Linux tool for iOS supervision enrollment")

        with gr.Tab("Organizations"):
            gr.Markdown("### Organization Management")

            with gr.Row():
                with gr.Column(scale=2):
                    org_dropdown = gr.Dropdown(
                        choices=orgs,
                        label="Organizations",
                        value=orgs[0] if orgs else None,
                        allow_custom_value=True,
                        info="Select an organization to view details",
                    )
                    org_details = gr.JSON(label="Organization Details", height=300)

                with gr.Column(scale=1):
                    gr.Markdown("#### Quick Actions")
                    with gr.Row():
                        refresh_btn = gr.Button(
                            "🔄 Refresh", size="sm", variant="secondary"
                        )

                    import_file = gr.File(
                        label="Import",
                        file_types=[".organization", ".zip", "*"],
                        file_count="single",
                        height=80,
                    )
                    import_btn = gr.Button("Import", variant="primary", size="sm")
                    import_out = gr.Textbox(label="", lines=2, interactive=False)

            with gr.Accordion("Create New Organization", open=False):
                with gr.Row():
                    with gr.Column(scale=2):
                        create_name = gr.Textbox(
                            label="Organization Name *", placeholder="My Organization"
                        )
                    with gr.Column():
                        create_id = gr.Textbox(
                            label="Organization ID", placeholder="com.example"
                        )
                    with gr.Column():
                        create_id = gr.Textbox(
                            label="Organization ID", placeholder="com.example"
                        )
                with gr.Row():
                    with gr.Column():
                        create_address = gr.Textbox(
                            label="Address", placeholder="123 Main St, City, ST 12345"
                        )
                    with gr.Column():
                        create_phone = gr.Textbox(
                            label="Phone", placeholder="8025551234"
                        )
                with gr.Row():
                    with gr.Column():
                        create_email = gr.Textbox(
                            label="Email", placeholder="admin@example.com"
                        )
                with gr.Row():
                    with gr.Column():
                        create_cert = gr.File(
                            label="Certificate (DER)",
                            file_types=[".der", ".crt", "*"],
                            file_count="single",
                            height=60,
                        )
                    with gr.Column():
                        create_key = gr.File(
                            label="Private Key (DER)",
                            file_types=[".der", ".key", "*"],
                            file_count="single",
                            height=60,
                        )
                with gr.Row():
                    create_btn = gr.Button("Create Organization", variant="primary")
                    create_out = gr.Textbox(label="", lines=2, interactive=False)

            with gr.Accordion("Export / Delete", open=False):
                with gr.Row():
                    export_path = gr.Textbox(
                        label="Export Path", placeholder="./my_org.zip", scale=3
                    )
                    export_btn = gr.Button("Export", variant="secondary", scale=1)
                export_out = gr.Textbox(label="", lines=2, interactive=False)
                gr.Markdown("---")
                gr.Markdown("**Delete Organization** (cannot be undone)")
                delete_warning = gr.Markdown(
                    "⚠️ This will permanently delete the selected organization"
                )
                delete_btn = gr.Button("Delete Selected", variant="stop")
                delete_out = gr.Textbox(label="", lines=2, interactive=False)

        with gr.Tab("Devices"):
            gr.Markdown("### Device Selection")
            device_dropdown = gr.Dropdown(
                choices=devices,
                label="Device UDID",
                value=devices[0] if devices else None,
                allow_custom_value=True,
            )
            refresh_devices_btn = gr.Button("🔄 Refresh Devices", variant="secondary")
            gr.Markdown(f"*{len(devices)} device(s) found*")

        with gr.Tab("Enrollment Actions"):
            gr.Markdown(
                "### Select organization and device from the tabs above, then choose an action"
            )

            with gr.Tab("Erase"):
                skip_esim = gr.Checkbox(
                    label="Skip eSIM (keep cellular data)", value=False
                )
                erase_btn = gr.Button("Erase Device", variant="stop")
                erase_out = gr.Textbox(label="Output", lines=5, interactive=False)
                erase_btn.click(
                    fn=erase, inputs=[device_dropdown, skip_esim], outputs=erase_out
                )

            with gr.Tab("Update"):
                skip_completed = gr.Checkbox(
                    label="Skip update completed pane", value=False
                )
                update_btn = gr.Button("Update to Latest iOS", variant="primary")
                update_out = gr.Textbox(label="Output", lines=5, interactive=False)
                update_btn.click(
                    fn=update,
                    inputs=[device_dropdown, skip_completed],
                    outputs=update_out,
                )

            with gr.Tab("Make Supervised"):
                gr.Markdown(
                    "Make the device supervised using the selected organization's certificate"
                )
                supervised_btn = gr.Button("Make Supervised", variant="primary")
                supervised_out = gr.Textbox(label="Output", lines=5, interactive=False)
                supervised_btn.click(
                    fn=make_supervised,
                    inputs=[device_dropdown, org_dropdown],
                    outputs=supervised_out,
                )

            with gr.Tab("Restore"):
                ipsw_file = gr.File(label="Select IPSW File")
                skip_restore = gr.Checkbox(
                    label="Skip restore completed pane", value=False
                )
                restore_btn = gr.Button("Restore IPSW", variant="stop")
                restore_out = gr.Textbox(label="Output", lines=5, interactive=False)
                restore_btn.click(
                    fn=restore,
                    inputs=[device_dropdown, ipsw_file, skip_restore],
                    outputs=restore_out,
                )

            with gr.Tab("Info"):
                info_btn = gr.Button("Get Device Info", variant="secondary")
                info_out = gr.Textbox(label="Output", lines=10, interactive=False)
                info_btn.click(fn=get_info, inputs=[device_dropdown], outputs=info_out)

            with gr.Tab("Full Enrollment"):
                gr.Markdown("Run complete enrollment workflow on selected device")
                full_btn = gr.Button("Full Enrollment", variant="primary")
                full_out = gr.Textbox(label="Output", lines=10, interactive=False)
                full_btn.click(
                    fn=full_enroll,
                    inputs=[device_dropdown, org_dropdown],
                    outputs=full_out,
                )

        def on_org_select(name):
            if not name:
                return {}, ""
            details = get_org_details(name)
            try:
                details_dict = json.loads(details)
            except:
                details_dict = {"raw": details}
            return details_dict, details

        def on_refresh():
            new_orgs = get_orgs()
            return (
                gr.update(choices=new_orgs, value=new_orgs[0] if new_orgs else None),
                {} if not new_orgs else get_org_details(new_orgs[0]),
                "",
            )

        def on_import(file_obj):
            if file_obj is None:
                return "Error: No file selected"
            file_path = getattr(file_obj, "name", None) or str(file_obj)
            result = run_enroll(["org", "import", "--path", file_path])
            new_orgs = get_orgs()
            return (
                result,
                gr.update(choices=new_orgs, value=new_orgs[-1] if new_orgs else None),
                {},
            )

        def on_export(name, path):
            if not name:
                return "Error: No organization selected"
            if not path:
                return "Error: No destination path specified"
            result = run_enroll(["org", "export", "--name", name, "--path", path])
            return result

        def on_delete(name):
            if not name:
                return "Error: No organization selected"
            if not name.startswith("_"):  # prevent accidental deletion
                pass  # allow deletion
            result = run_enroll(["org", "delete", "--name", name])
            new_orgs = get_orgs()
            return (
                result,
                gr.update(choices=new_orgs, value=new_orgs[0] if new_orgs else None),
                {},
            )

        def on_create(name, org_id, address, phone, email, cert_obj, key_obj):
            if not name:
                return "Error: Organization name is required"
            args = ["org", "create", "--name", name]
            if org_id:
                args.extend(["--org-id", org_id])
            if address:
                args.extend(["--address", address])
            if phone:
                args.extend(["--phone", phone])
            if email:
                args.extend(["--email", email])
            if cert_obj:
                cert_path = getattr(cert_obj, "name", None) or str(cert_obj)
                args.extend(["--cert", cert_path])
            if key_obj:
                key_path = getattr(key_obj, "name", None) or str(key_obj)
                args.extend(["--key", key_path])
            result = run_enroll(args)
            new_orgs = get_orgs()
            return (
                result,
                gr.update(
                    choices=new_orgs,
                    value=name
                    if name in new_orgs
                    else (new_orgs[-1] if new_orgs else None),
                ),
                {},
            )

        org_dropdown.change(
            fn=on_org_select, inputs=[org_dropdown], outputs=[org_details, import_out]
        )
        refresh_btn.click(
            fn=on_refresh, outputs=[org_dropdown, org_details, import_out]
        )
        import_btn.click(
            fn=on_import,
            inputs=[import_file],
            outputs=[import_out, org_dropdown, org_details],
        )
        export_btn.click(
            fn=on_export, inputs=[org_dropdown, export_path], outputs=[export_out]
        )
        delete_btn.click(
            fn=on_delete,
            inputs=[org_dropdown],
            outputs=[delete_out, org_dropdown, org_details],
        )
        create_btn.click(
            fn=on_create,
            inputs=[
                create_name,
                create_id,
                create_address,
                create_phone,
                create_email,
                create_cert,
                create_key,
            ],
            outputs=[create_out, org_dropdown, org_details],
        )

    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=False,
    )


if __name__ == "__main__":
    main()
