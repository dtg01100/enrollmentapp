# ios-enroll org commands

Organization management commands. See the [index](README.md) for global
options and the shared contracts (confirmation, redaction, JSON output).

> **After pulling new code:** keep the venv in sync with an *editable*
> install — `uv pip install -e .` — and re-run it after every `git pull`.
> A non-editable copy in `site-packages` is a snapshot from install time, so
> a stale install silently misses flags documented here (e.g.
> `org list --json`). An editable install always tracks the working
> tree; if a documented flag is missing, refresh it.

## `org list`

List stored organizations.

| Flag | Description |
|---|---|
| `--json` | JSON output (see the [JSON contract](README.md#json-output)) |
| `--verbose` | also show org ID, MDM URL, cert/key status |

## `org create`

Create an organization. Fails if the name already exists.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required; `[A-Za-z0-9._-]` only) |
| `--org-id <ID>` | organization identifier |
| `--address`, `--phone`, `--email` | optional contact info |
| `--mdm-url <URL>`, `--checkin-url <URL>`, `--mdm-topic <TOPIC>`, `--mdm-description <TEXT>` | MDM metadata |
| `-C` / `--cert <PATH>`, `-K` / `--key <PATH>` | supervising identity files |
| `--wifi-config <FILE>` | WiFi mobileconfig to attach |

## `org delete`

Delete an organization — including any supervising cert/key, WiFi config,
and metadata. Confirms first (warns about identity loss when a cert+key
exists); `--yes` for scripts.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--yes` / `-y` | skip the confirmation prompt |

## `org show`

Show an organization's details.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |

## `org set-cert`

Update an existing org's certificate path.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `-C` / `--cert <PATH>` | certificate file |

## `org set-key`

Update an existing org's private key path.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `-K` / `--key <PATH>` | private key file |

## `org set-mdm-url`

Update an existing org's MDM server URL.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--mdm-url <URL>` | MDM server URL |

## `org set-checkin-url`

Update an existing org's SCEP check-in URL.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--checkin-url <URL>` | SCEP check-in URL |

## `org set-mdm-topic`

Update an existing org's MDM topic.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--mdm-topic <TOPIC>` | MDM topic |

## `org import`

Import an organization from an Apple Configurator `.organization` file, a
directory, or a ZIP. Importing over an org with the **same name replaces
it** (old identity/files are deleted) — that case confirms first.

| Flag | Description |
|---|---|
| `--path <FILE|DIR|ZIP>` | import source (required) |
| `-p` / `--password <PASSWORD>` | password for `.organization` PKCS12 (defaults to `"password"`) |
| `--yes` / `-y` | skip the overwrite confirmation |

## `org import-mobileconfig`

Import an organization from an MDM `.mobileconfig` file (extracts MDM
metadata and generates a fresh supervision identity). Fails if the org
already exists.

| Flag | Description |
|---|---|
| `--path <FILE>` | `.mobileconfig` file (required) |

## `org set-wifi`

Attach a WiFi mobileconfig to an organization (installed on devices during
supervised enrollment). Replacing an existing config confirms first.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--path <FILE>` | WiFi `.mobileconfig` file (required) |
| `--yes` / `-y` | skip the replace confirmation |

## `org export`

Export an organization to a directory or ZIP.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--path <DIR|ZIP>` | destination (required; `.zip` suffix produces a ZIP) |

## `org generate`

Generate a new supervising identity (self-signed cert + RSA key) and save
the org with the given MDM configuration. If the org already exists, the org
directory is replaced — that case confirms first.

| Flag | Description |
|---|---|
| `--name <NAME>` | organization name (required) |
| `--org-id`, `--mdm-url`, `--checkin-url`, `--mdm-topic`, `--mdm-description` | org metadata |
| `--valid-days <DAYS>` | identity validity (default 1825 = 5 years) |
| `--yes` / `-y` | skip the replace confirmation |
