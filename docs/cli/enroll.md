# ios-enroll enroll commands

Enrollment commands. See the [index](README.md) for global options and the
shared contracts (confirmation, redaction, JSON output).

## `enroll guided-enroll`

Fully interactive guided workflow: device selection, org selection, skip
panes, WiFi, and supervised enrollment. No flags.

## `enroll make-supervised`

Apply supervision (and optionally MDM enrollment) to a device using an
org's cert/key.

| Flag | Description |
|---|---|
| `--udid <UDID>` | target device (prompts if omitted) |
| `--org-name <NAME>` | organization to enroll under (required) |
| `--skip-preset <PRESET>` | `minimal`, `standard`, or `all` |
| `--skip <PANE>` | individual pane(s) to skip (repeatable) |
| `--wifi-ssid <SSID>`, `--wifi-password <PASSWORD>`, `--wifi-encryption <ENC>` | WiFi to install (`WPA`/`WEP`/`None`) |
| `--wifi-config <FILE>` | WiFi `.mobileconfig` to install |
| `--mdm-mobileconfig <FILE>` | MDM enrollment profile |
| `--mdm-unremovable` | make the MDM profile non-removable |
| `--fail-on-mdm-error` / `--no-fail-on-mdm-error` | exit non-zero if MDM enrollment fails (default: fail) |
| `-v` / `--verbose` | show progress updates |

## `enroll re-enroll`

Erase a device's cloud configuration so it can be re-enrolled. Confirms
first; `--force` for scripts.

| Flag | Description |
|---|---|
| `--udid <UDID>` | target device (prompts if omitted) |
| `-f` / `--force` | skip the confirmation prompt |

## `enroll status`

Show a device's activation, supervision, and MDM state.

| Flag | Description |
|---|---|
| `--udid <UDID>` | target device (prompts if omitted) |

## `enroll validate`

Validate an org's enrollment prerequisites (cert, key, MDM URL) without
touching devices.

| Flag | Description |
|---|---|
| `--org-name <NAME>` | organization (prompts if omitted) |
| `--mdm-url <URL>` | override the MDM URL to check |
| `--check-mdm` | verify the MDM server is reachable |

## `enroll activate`

Activate a paired device.

| Flag | Description |
|---|---|
| `--udid <UDID>` | target device |
