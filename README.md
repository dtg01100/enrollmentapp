# ios-enroll

iOS device supervised enrollment CLI for Linux — an Apple Configurator alternative.

## Why

Apple Configurator requires macOS to supervise and enroll iOS devices. This project provides the same functionality on Linux, enabling:

- Supervised device enrollment via command line
- MDM profile installation without macOS
- Automated enrollment workflows
- Organization-based certificate management

## Installation

### System Dependencies

**usbmuxd** provides the device communication socket:

```bash
# Debian/Ubuntu
sudo apt install usbmuxd

# Fedora
sudo dnf install usbmuxd

# Arch
sudo pacman -S usbmuxd
```

**Apple Device USB Rules** (for non-root device access):

```bash
# Manually set up udev rules for Apple devices
sudo tee /etc/udev/rules.d/99-apple-device.rules << 'EOF'
# Apple iPhone, iPad, iPod
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", MODE="0666"
# Apple iPhone (CDC Ethernet)
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", ATTR{idProduct}=="12*", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### Install ios-enroll

```bash
# With uv (recommended)
uv tool install .

# With pip
pip install .

# Editable/dev install
pip install -e .
```

## Usage

> Full command reference: [`docs/cli/`](docs/cli/README.md) — every command, flag, and output contract.

### Device Commands

```bash
ios-enroll device list [--json] [--verbose]              # List connected devices
ios-enroll device info [--udid <UDID>] [--json]          # Get device info
ios-enroll device list-apps [--udid <UDID>] [--type Any|User|System] [--json]   # Installed apps
ios-enroll device network [--udid <UDID>] [--json]       # SSID, IPs, DNS, proxy
ios-enroll device certs [--udid <UDID>] [--json]         # Provisioning profiles
ios-enroll device security-info [--udid <UDID>] [--json] # Passcode, lock, battery
```

### Profile Commands

```bash
ios-enroll profile list [--udid <UDID>] [--json]                       # Installed config profiles
ios-enroll profile remove <identifier> [--udid <UDID>] [--yes]         # Remove a config profile (asks first)
```

`profile remove` is destructive — it confirms on a TTY and requires `--yes`
in non-interactive (scripted/CI) runs, where there is no prompt.

The four `device network` / `device certs` / `device security-info` /
`device list-apps` commands and the two `profile list` / `profile remove`
commands mirror the macOS `mdmclient` tool's surface (`QueryInstalledApps`,
`QueryNetworkInformation`, `QueryCertificates`, `QuerySecurityInfo`,
`QueryInstalledProfiles`, `removeSystemProfile`) so the same MDM inspection
workflows run on Linux without Apple Configurator. The GUI has a dedicated
**MDM** tab (next to Devices / Organizations / Enrollment / Restore) that
renders the same data as tables and side-by-side info panels, and the
Devices-tab right-click menu exposes "Show MDM Info" and a "Remove Profile"
sub-menu.

### Organization Commands

```bash
ios-enroll org list [--json]                           # List organizations (--json for scripts)
ios-enroll org create --name "My Org"                    # Create organization
ios-enroll org delete --name "My Org" [--yes]            # Delete organization (asks first; --yes for scripts)
ios-enroll org show --name "My Org"                      # Show organization details
ios-enroll org import --path <file|dir|zip> [--yes]      # Import (replaces same-named org — asks first)
ios-enroll org export --name "My Org" --path <dir|zip>   # Export organization
ios-enroll org generate --name "My Org" [--yes]          # Generate identity (asks first if org exists)
ios-enroll org set-cert --name "My Org" -C cert.der      # Set certificate
ios-enroll org set-key --name "My Org" -K key.der        # Set private key
ios-enroll org set-mdm-url --name "My Org" --mdm-url <URL>  # Set MDM URL
ios-enroll org set-checkin-url --name "My Org" --checkin-url <URL>   # Set check-in URL
ios-enroll org set-mdm-topic --name "My Org" --mdm-topic <topic>     # Set MDM topic
ios-enroll org import-mobileconfig --path <file>             # Import from MDM .mobileconfig
ios-enroll org set-wifi --name "My Org" --path <file> [--yes]  # Attach WiFi config (asks first if replacing)
```

`org delete`, importing over an existing org, regenerating an identity, and
replacing a WiFi config are destructive — each asks for confirmation on an
interactive terminal and requires `--yes` in non-interactive (scripted/CI)
runs, where there is no prompt.

### Enrollment Commands

```bash
ios-enroll enroll guided-enroll                                          # Guided interactive enrollment
ios-enroll enroll make-supervised --udid <UDID> --org-name "My Org"     # Make supervised
ios-enroll enroll activate --udid <UDID>                                 # Activate device
ios-enroll enroll re-enroll --udid <UDID>                                # Clear config for re-enrollment
ios-enroll enroll status --udid <UDID>                                   # Show enrollment status
ios-enroll enroll validate                                               # Validate prerequisites
```

### Other

```bash
ios-enroll --version                                     # Show version
ios-enroll --gui                                         # Launch the GUI
ios-enroll-gui                                           # GUI entry script (same thing, separate console_scripts entry)

# Optional install for the GUI
pip install 'ios-enroll[gui]'    # or: uv tool install 'ios-enroll[gui]'
```

### Restore Commands

```bash
ios-enroll device restore --udid <UDID> --list-versions [--json]  # List signed iOS versions (--json for scripts)
ios-enroll device restore --udid <UDID> --ipsw <path> [--yes]   # Restore a local .ipsw file (asks first)
ios-enroll device restore --show-cache [--json]            # Show firmware cache contents (--json for scripts)
ios-enroll device restore --clear-cache [--yes]            # Remove downloaded IPSW files (asks first)
ios-enroll device restore --cache-dir <DIR>                # Override the firmware cache location
```

A restore erases the device, and `--clear-cache` deletes downloaded IPSW
files — both ask for confirmation on an interactive terminal. In
non-interactive (scripted/CI) runs there is no prompt, so pass `--yes` to
confirm; without it the command refuses to run rather than silently wiping
the device or cache.

`idevicerestore` performs the restore (with a `pymobiledevice3` fallback when
it's not on PATH). There is **no timeout** on the restore subprocess — older
iPads can take 45-60+ minutes. For long restores, run via `tmux`/`screen` or
the agent's background mode so the terminal session doesn't kill the process.

The firmware cache (4-7 GB per IPSW) is resolved with 4-tier precedence:

1. `--cache-dir <DIR>` (or the GUI "Cache folder..." button)
2. `IOS_ENROLL_CACHE_DIR` env var
3. `~/.config/ios-enroll/config.json` field `cache_dir`
4. `~/.cache/ios-enroll/firmware/` (XDG default)

Downloads resume via HTTP `Range:` after a partial transfer. The default is
never `/tmp` (tmpfs quota can OOM the host on large IPSW downloads).

The GUI also has a **Restore tab** (next to Devices / Organizations /
Enrollment): pick a device, refresh the signed-version dropdown (or browse
for a local `.ipsw`), and click Start Restore. The cache folder is
configurable from the tab, and the live `idevicerestore` output streams into
the tab's log panel.

## Machine-Readable Output (`--json`)

Several commands accept `--json` for scripting. The contract is uniform
across all of them:

- **stdout is always valid JSON** — never prose — so it can be piped straight
  into a parser (`ios-enroll org list --json | jq .`).
- **Empty result sets stay parseable**: list commands emit `[]`; `--show-cache`
  always emits the full object with zeroed fields.
- **Failures emit `{"error": "..."}`** — check for an `error` key. Usage
  errors (e.g. `device info --json` without `--udid`) also exit non-zero.
- **JSON is raw/unredacted** (machine-readable); redaction applies only to
  the human-readable text output.

| Command | JSON output |
|---|---|
| `device list --json` | array of `{udid, name, type, ios_version, build_version, ecid}` — `[]` when no devices |
| `device info --json` | object `{udid, name, type, ios_version, build_version, ecid}` (requires `--udid`) |
| `device restore --show-cache --json` | object `{path, size_bytes, ipsw_count, ipsw_files}` |
| `device restore --list-versions --json` | array of `{version, build, url, device, display_label}` |
| `org list --json` | array of `{name, org_id, mdm_url, checkin_url, mdm_topic, has_cert, has_key, wifi_config_path}` — `[]` when no orgs |

```bash
# Names of orgs that have a supervising identity
ios-enroll org list --json | jq -r '.[] | select(.has_cert) | .name'

# Bytes used by the firmware cache
ios-enroll device restore --show-cache --json | jq -r '.size_bytes'

# First signed iOS version for a device
ios-enroll device restore --udid <UDID> --list-versions --json | jq -r '.[0].display_label'
```

## Organization Storage

Organizations are stored in `~/.config/apple_device_cli/orgs/` by default. Each org directory contains `org.json` and optionally `cert.der` and `key.der`.

## Requirements

- Python 3.10+
- pymobiledevice3 (primary device interaction library)
- cryptography (certificate and key generation)

## License

**MIT**

See `LICENSE` for the full license text.
