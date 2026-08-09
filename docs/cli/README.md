# ios-enroll CLI Reference

Complete reference for the `ios-enroll` command-line interface. The
[README](../../README.md) is a quick start; this index documents the global
options and the contracts that apply to every command. Each command group
has its own page.

- [Global options](#global-options)
- [Contracts](#contracts)
  - [Confirmation for destructive actions](#confirmation-for-destructive-actions)
  - [Redaction](#redaction)
  - [JSON output](#json-output)
- [Command groups](#command-groups)
- [Exit codes](#exit-codes)
- [Development setup](#development-setup)

## Global options

```
ios-enroll                          banner + command list
ios-enroll --version                print version and exit
ios-enroll --gui                    launch the GUI (requires the [gui] extra)
ios-enroll device|org|enroll        group help (each group also prints a list
                                    of its commands when invoked bare)
ios-enroll <command> --help         per-command help with all flags
```

## Contracts

### Confirmation for destructive actions

Every destructive action asks for confirmation on an interactive terminal and
requires an explicit opt-in flag in non-interactive (scripted/CI) runs —
there is no prompt off a TTY, so without the flag the command **refuses to
run** and exits 1.

| Command / action | Destructive effect | Flag |
|---|---|---|
| `device restore ...` | wipes the device | `--yes` / `-y` |
| `device restore --clear-cache` | deletes downloaded IPSW files | `--yes` / `-y` |
| `org delete` | deletes the org dir (cert/key, wifi config, metadata) | `--yes` / `-y` |
| `org import` (same-named org exists) | replaces the existing org | `--yes` / `-y` |
| `org generate` (org exists) | replaces the org dir (regenerates identity) | `--yes` / `-y` |
| `org set-wifi` (wifi config exists) | replaces the org's WiFi config | `--yes` / `-y` |
| `enroll re-enroll` | erases device cloud config | `--force` / `-f` |

Declining a prompt prints `Cancelled.` and exits 1.

### Redaction

Human-readable output redacts sensitive values (UDIDs, org names, URLs,
paths, phones/emails) to keep logs shareable. Machine-readable `--json`
output is **raw/unredacted** by design.

### JSON output (`--json`)

The `--json` contract is uniform across every command that supports it:

- **stdout is always valid JSON** — never prose — so it can be piped straight
  into a parser (`ios-enroll org list --json | jq .`).
- **Empty result sets stay parseable**: list commands emit `[]`; `--show-cache`
  always emits the full object with zeroed fields.
- **Failures emit `{"error": "..."}`** — check for an `error` key. Usage
  errors (e.g. `device info --json` without `--udid`) also exit non-zero.

| Command | JSON output |
|---|---|
| `device list --json` | array of `{udid, name, type, ios_version, build_version, ecid}` — `[]` when no devices |
| `device info --json` | object `{udid, name, type, ios_version, build_version, ecid}` (requires `--udid`) |
| `device restore --show-cache --json` | object `{path, size_bytes, ipsw_count, ipsw_files}` |
| `device restore --list-versions --json` | array of `{version, build, url, device, display_label}` |
| `org list --json` | array of `{name, org_id, mdm_url, checkin_url, mdm_topic, has_cert, has_key, wifi_config_path}` — `[]` when no orgs |

## Command groups

| Group | Page | Commands |
|---|---|---|
| `device` | [device.md](device.md) | `list`, `info`, `restore`, `verify-ipsw` |
| `org` | [org.md](org.md) | `list`, `create`, `delete`, `show`, `set-cert`, `set-key`, `set-mdm-url`, `set-checkin-url`, `set-mdm-topic`, `import`, `import-mobileconfig`, `set-wifi`, `export`, `generate` |
| `enroll` | [enroll.md](enroll.md) | `guided-enroll`, `make-supervised`, `re-enroll`, `status`, `validate`, `activate` |

## Exit codes

- `0` — success (including JSON error objects on some legacy error paths).
- `1` — usage errors, refused/cancelled confirmations, failed operations
  (e.g. restore failure, `verify-ipsw` mismatch, `device info --json`
  without `--udid`).

## Development setup

For development, install the package **editable** so the CLI always tracks
the working tree:

```bash
uv pip install -e .
```

A non-editable install copies the package into `site-packages` at install
time — a snapshot that goes stale after `git pull`. A stale install
silently misses flags documented on these pages (e.g. `device restore
--json`); if a documented flag is missing, refresh the install with the
command above. (The group pages carry a short version of this note; see
[device.md](device.md).)

## See also

- [README](../../README.md) — quick start
- [ENROLLMENT_FLOWS.md](../ENROLLMENT_FLOWS.md) — enrollment flow architecture
- [INSTALL_WINDOWS.md](../INSTALL_WINDOWS.md) — Windows install + GUI launch
