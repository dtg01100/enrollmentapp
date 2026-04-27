# ios-enroll

iOS device supervised enrollment CLI for Linux — an Apple Configurator alternative.

## Installation

```bash
uv tool install .
```

## Usage

### Device Commands

```bash
ios-enroll device list                                   # List connected devices
ios-enroll device info [--udid <UDID>]                   # Get device info
ios-enroll device erase --udid <UDID>                    # Erase device
ios-enroll device update --udid <UDID>                   # Update device to latest iOS
ios-enroll device restore --udid <UDID> --ipsw <PATH>    # Restore with IPSW
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

- Python 3.8+
- pymobiledevice3 (primary device interaction library)
- libimobiledevice (idevicepair, ideviceinfo — for basic device enumeration)

## License

MIT
