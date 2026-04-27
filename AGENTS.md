# AGENTS.md

Purpose
-------
This document collects non-obvious knowledge an autonomous agent needs to work effectively in this repository. It focuses on project-level commands, architecture, important patterns and gotchas discovered by reading the code and docs (README.md, SPEC.md, and source files).

Quick checks
------------
- Not empty: repository contains a working Python package under src/apple_device_cli, tests/, a legacy top-level script (enroll.py), and supporting assets (mobileconfig/.organization files, homebrew scripts).
- Primary library entrypoint: src/apple_device_cli (Typer-based CLI implemented in src/apple_device_cli/cli.py).

Essential commands
------------------
- Install (development / local):
  - The README suggests: `uv tool install .` (project includes `uv.lock` and README mentions `uv`).
  - Project packaging in pyproject.toml uses hatchling; wheel target packages `src/apple_device_cli` and defines a console script:
    - `ios-enroll = "apple_device_cli.cli:main"` (run after installation).
- Run CLI locally without installing (quick):
  - Run the legacy wrapper: `./enroll.py <command>` (executable script at repo root).
  - Or execute the package module (interpreted): `python -m apple_device_cli.cli` (module exposes `main()` and Typer app).
- Tests:
  - Run unit tests with pytest: `python -m pytest tests/ -v` (also referenced in docs and plans).
  - Run a single test file: `pytest tests/test_org_manager.py -v` (examples in docs).

Environment & external dependencies
-----------------------------------
The code depends on system binaries and libraries that are not Python-only. Agents should ensure these are available or know tests may mock them:
- libimobiledevice tools (idevicepair, ideviceinfo, ideviceenterrecovery). These are used via subprocess to enumerate/query devices.
- idevicerestore (used for erase/restore/update flows). The code tries to locate it via `brew --prefix` and expects a Homebrew-installed idevicerestore in PATH.
- openssl (used to process .mobileconfig files via `openssl smime -verify`).
- Python packages listed in pyproject.toml: typer, pymobiledevice3, cryptography. `pymobiledevice3` is imported in code paths that interact with devices and may not be available in all environments.
- Homebrew helper scripts are included in `homebrew/` and are referenced from docs (installation for Linux).

Project structure and key files
-------------------------------
- pyproject.toml - defines package metadata, dependencies, and console script `ios-enroll` (apple_device_cli.cli:main).
- README.md & SPEC.md - high-level usage, installation, and specification; SPEC.md contains extensive notes and examples that reflect expected behaviour.
- enroll.py - legacy/alternative top-level CLI script (argparse-based). Useful for manual testing and examples in SPEC.md.
- src/apple_device_cli/ - primary package with the Typer CLI implementation and subpackages:
  - cli.py - Typer CLI entrypoint and command wiring (device, org, enroll subcommands).
  - device/ - device interaction helpers (connection, info, state).
  - enrollment/ - enrollment flows (activation, supervised pairing, skip_panes handling).
  - orgs/ - organization management (manager.py and identity handling). Key file: orgs/manager.py
  - restore/ - restore/erase/update helpers that invoke idevicerestore.
  - core/ - common exceptions and small core utilities.
- tests/ - pytest test suite covering org manager, enrollment flows and helpers (run with pytest).

Application architecture & control flow (high-level)
---------------------------------------------------
- CLI layer: `src/apple_device_cli/cli.py` (Typer) exposes commands for device, org, and enroll flows. The console script `ios-enroll` maps here.
- Org storage: `OrganizationManager` (src/apple_device_cli/orgs/manager.py) manages organizations on disk under DEFAULT_ORGS_DIR (Path.home()/.config/apple_device_cli/orgs). Each org directory contains `org.json` plus optional `cert.der` and `key.der`.
- Device interaction: device enumeration relies on external CLI tools (idevicepair/ideviceinfo) or `pymobiledevice3` for more advanced lockdown/service interactions.
- Enrollment flow: make-supervised does supervised pairing via `pymobiledevice3.lockdown` and then calls a MobileConfigService to set a cloud configuration; restore/erase/update use idevicerestore when available.
- Mobileconfig import: `OrganizationManager.import_mobileconfig` shells out to `openssl smime -verify -inform DER -noverify -in <file>` and then parses the resulting plist.
- Skip panes: skip pane presets and validation live in `src/apple_device_cli/enrollment/skip_panes.py` (VALID_PANES, PRESETS, resolve_skip_panes).

Important conventions & patterns
------------------------------
- Organization storage
  - Default path: ~/.config/apple_device_cli/orgs (constant DEFAULT_ORGS_DIR in manager.py).
  - Each org is a directory named by a sanitized org name (non-alphanumeric chars replaced with `_`). Directory contains `org.json`, and possibly `cert.der` and `key.der`.
  - `org.json` is the canonical metadata written by Organization.save(); `cert_path` and `key_path` are not written into org.json (they are kept as files in the directory and Organization.load sets their paths based on existence of cert.der/key.der).
  - Importing a `.organization` (Apple Configurator file) expects a PKCS12 identity embedded and uses a default password `password` when decoding (see import_org and _import_from_organization).

- Error patterns & messages
  - Many operations use subprocess and raise/provide ValueError or print friendly messages when external binaries or parsing fail (for example mobileconfig parsing errors include stderr from openssl). Tests assert against these messages in a few places.
  - For mobileconfig import: missing PayloadOrganization or existing organization name causes ValueError with exact messages looked for in tests (e.g., "Organization 'X' already exists" or "Missing PayloadOrganization in mobileconfig"). Avoid changing those strings without updating tests.

- Tests
  - Tests live under tests/ and use pytest. Several tests exercise org manager import flows and expect specific error messages.
  - Tests may assume certain command output strings; be careful when refactoring messages produced by OrganizationManager import/export functions.

Non-obvious gotchas & agent guidance
-----------------------------------
- External binaries are required for runtime behaviour; tests may mock or assert error messages when those binaries are missing. Expect failures if idevice* binaries, idevicerestore, or openssl are not present.
- Mobileconfig import uses openssl SMIME verify in DER mode and `-noverify`. If `openssl` is not available or the file is malformed, `import_mobileconfig` raises ValueError including the stderr content.
- The codebase contains two CLI implementations: the modern Typer-based package under `src/apple_device_cli/cli.py` (console script entrypoint) and a legacy argparse script at the repository root (`enroll.py`). Both expose similar behaviors; prefer `src/.../cli.py` for programmatic/integration changes and tests, but enroll.py is useful for manual/local testing.
- Organization import of `.organization` files assumes PKCS12 identity bytes are present in the plist (identityReference) sometimes base64-encoded; decoding failures will surface as "Failed to decode identity (wrong password?)" ValueError.
- The organization `save()` writes org.json but intentionally omits cert_path/key_path keys (these are copied to cert.der/key.der files). Tests and other code depend on this layout.
- DEFAULT_ORGS_DIR is a real path under the user's home directory; tests may temporarily change or mock this by constructing OrganizationManager with a custom `orgs_dir` Path.
- `pymobiledevice3` imports are present in codepaths that interact with devices. Static type checkers may report these imports as missing in environments that don't have pymobiledevice3 installed; that's expected unless running on a machine prepared for device interactions.

Files and locations to inspect first (for debugging or changes)
--------------------------------------------------------------
- src/apple_device_cli/cli.py : command wiring, user-facing messages (tests may match strings here)
- src/apple_device_cli/orgs/manager.py : organization storage, import/export logic, mobileconfig handling
- src/apple_device_cli/enrollment/skip_panes.py : valid panes and presets validation
- enroll.py : legacy CLI with many usage examples and an alternate implementation of flows
- pyproject.toml : dependencies and console script name
- README.md and SPEC.md : usage examples and design spec (good to review to preserve behaviour expected by docs/tests)

Safe modification rules (suggested)
----------------------------------
- When changing user-facing messages (especially in org import/export and error messages), run the test suite; tests assert exact message text in multiple places.
- If adding or changing code that shells out to system binaries, prefer to keep the external command's stderr/content intact in error messages so tests that examine them continue to behave.
- If you change organization storage layout, update Organization.load/save and adjust tests accordingly.

Where to run tests & typical flow for an agent
----------------------------------------------
1. Create and activate a Python virtualenv (this repo contains a .venv but you can create one).
2. Install dev dependencies listed in pyproject.toml or at minimum install pytest and cryptography.
3. Run `python -m pytest tests/ -v`. Fix failing tests and update behaviours carefully.

References for deeper reading
----------------------------
- SPEC.md — design notes and usage examples included in the repository (good high-level reference).
- README.md — quick install and usage examples (mentions `uv tool install .`).
- src/apple_device_cli/orgs/manager.py — org storage and import/export logic (critical for most changes involving orgs).
- src/apple_device_cli/enrollment/* — supervised pairing, skip_panes, activation flows.

If something appears missing
---------------------------
Only include observed commands and files. If you don't find CI, linting configuration, or packaging hooks you expect, do not invent them — instead look at pyproject.toml and homebrew/ for hints.

Last notes
----------
- Be conservative changing strings used in tests.
- Expect device-related code to require hardware or mocks; design changes that isolate external calls (wrap subprocess and network calls) will make unit testing easier.


