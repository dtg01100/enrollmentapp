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

**libimobiledevice** and **usbmuxd** are required for device communication:

```bash
# Debian/Ubuntu
sudo apt install libimobiledevice6 usbmuxd

# Fedora
sudo dnf install libimobiledevice usbmuxd

# Arch
sudo pacman -S libimobiledevice usbmuxd
```

**Apple Device USB Rules** (for non-root device access):

```bash
# Debian/Ubuntu (comes with libimobiledevice)
sudo cp /usr/share/doc/libimobiledevice/usbmuxd.conf /etc/usbmuxd.conf
sudo systemctl restart usbmuxd

# Or manually set up udev rules for Apple devices
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
ios-enroll device list                                   # List connected devices
ios-enroll device info [--udid <UDID>]                   # Get device info
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
```

### Enrollment Commands

```bash
ios-enroll enroll guided-enroll                                        # Guided interactive enrollment
ios-enroll enroll make-supervised --udid <UDID> --org-name "My Org"   # Make supervised
ios-enroll enroll activate --udid <UDID>                               # Activate device
```

### Other

```bash
ios-enroll version                                      # Show version
```

## Organization Storage

Organizations are stored in `~/.config/apple_device_cli/orgs/` by default. Each org directory contains `org.json` and optionally `cert.der` and `key.der`.

## Requirements

- Python 3.10+
- pymobiledevice3 (primary device interaction library)
- libimobiledevice (idevicepair, ideviceinfo — for basic device enumeration)

## License

**MIT**

See `LICENSE` for the full license text.
