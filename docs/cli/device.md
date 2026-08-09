# ios-enroll device commands

Device management commands. See the [index](README.md) for global options
and the shared contracts (confirmation, redaction, JSON output).

## `device list`

List connected iOS devices (via usbmuxd / pymobiledevice3).

| Flag | Description |
|---|---|
| `--json` | JSON output (see the [JSON contract](README.md#json-output)) |
| `--verbose` | also show type, iOS version, build, ECID |

## `device info`

Show properties of one device (UDID, name, type, iOS version, build, ECID).

| Flag | Description |
|---|---|
| `--udid <UDID>` | target device. With `--json` this is **required** (the interactive picker can't run in scripts) |
| `--json` | JSON output |

Without `--udid`, lists devices and prompts for a selection interactively.

## `device restore`

Restore a device to a signed iOS version or a local `.ipsw` file.

| Flag | Description |
|---|---|
| `--udid <UDID>` | target device in Normal mode |
| `--ecid <ECID>` | target a Recovery/restore/DFU-mode device (invisible to usbmuxd); mutually exclusive with `--udid` |
| `--ipsw <PATH>` | restore a local `.ipsw` file (skips the version dropdown) |
| `--list-versions` | print the signed versions for the device and exit |
| `--cache-dir <DIR>` | override the firmware cache location |
| `--show-cache` | print the cache state and exit |
| `--clear-cache` | delete all cached IPSW files and exit |
| `--json` | JSON output for `--show-cache` / `--list-versions` |
| `--yes` / `-y` | skip the confirmation prompt (see the [confirmation contract](README.md#confirmation-for-destructive-actions)) |

Notes:

- The device must be trusted by the host; `--ecid` targets Recovery/DFU-mode
  devices directly. Older iPads can take 45-60+ minutes — run via
  `tmux`/`screen` or a background terminal.
- Cache precedence: `--cache-dir` > `IOS_ENROLL_CACHE_DIR` >
  `~/.config/ios-enroll/config.json` `cache_dir` > `~/.cache/ios-enroll/firmware/`.
  Downloads resume via HTTP `Range:` after a partial transfer.

## `device verify-ipsw`

Verify a local IPSW against the hashes published by ipsw.me (streams
SHA-1 + SHA-256).

| Flag | Description |
|---|---|
| `--ipsw <PATH>` | IPSW file to verify (required) |
| `--device <PRODUCT_TYPE>` | ProductType (e.g. `iPad15,7`); defaults to parsing the filename |

Exit 0 on a full match (or when expected hashes are unavailable); exit 1 on
any mismatch.
