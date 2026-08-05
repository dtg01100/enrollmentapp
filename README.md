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

### Device Commands

```bash
ios-enroll device list [--json] [--verbose]              # List connected devices
ios-enroll device info [--udid <UDID>] [--json]          # Get device info
```

### Organization Commands

```bash
ios-enroll org list                                      # List organizations
ios-enroll org create --name "My Org"                    # Create organization
ios-enroll org delete --name "My Org"                    # Delete organization
ios-enroll org show --name "My Org"                      # Show organization details
ios-enroll org import --path <file|dir|zip>              # Import from .organization, dir, or zip
ios-enroll org export --name "My Org" --path <dir|zip>   # Export organization
ios-enroll org generate --name "My Org"                  # Generate supervising identity
ios-enroll org set-cert --name "My Org" -C cert.der      # Set certificate
ios-enroll org set-key --name "My Org" -K key.der        # Set private key
ios-enroll org set-mdm-url --name "My Org" --mdm-url <URL>  # Set MDM URL
ios-enroll org set-checkin-url --name "My Org" --checkin-url <URL>   # Set check-in URL
ios-enroll org set-mdm-topic --name "My Org" --mdm-topic <topic>     # Set MDM topic
ios-enroll org import-mobileconfig --path <file>             # Import from MDM .mobileconfig
ios-enroll org set-wifi --name "My Org" --wifi-config <file> # Attach WiFi config
```

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

## Organization Storage

Organizations are stored in `~/.config/apple_device_cli/orgs/` by default. Each org directory contains `org.json` and optionally `cert.der` and `key.der`.

## Requirements

- Python 3.10+
- pymobiledevice3 (primary device interaction library)
- cryptography (certificate and key generation)

## License

**MIT**

See `LICENSE` for the full license text.
