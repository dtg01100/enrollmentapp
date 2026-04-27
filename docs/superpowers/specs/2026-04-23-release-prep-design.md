# Release Preparation Design

**Date:** 2026-04-23
**Target:** Internal/shareable release of ios-enroll v0.1.0
**Scope:** Thorough polish — cleanup, documentation, license, tagging

## Context

ios-enroll has no releases or tags. The working tree has uncommitted changes (modified source, deleted legacy files). The repo contains junk artifacts (IPSW files, restore logs, cert/key files, Apple Configurator.app). Documentation is stale (references deleted `enroll.py`, wrong CLI name, old paths). No LICENSE file despite README claiming MIT.

## Decisions

- **Release type:** Internal/shareable (not PyPI/GitHub release)
- **CLI name:** `ios-enroll` (matches pyproject.toml entry point)
- **Junk files:** Add to .gitignore, delete `Apple Configurator.app/`
- **enroll_gui.py:** Delete (references deleted enroll.py, broken, Gradio not a dependency)
- **PKCS#7 warning:** Suppress with warnings.catch_warnings() like existing pattern

## Changes

### 1. Commit current working changes
Stage modified and deleted files as a single commit.

### 2. Expand .gitignore
Add: `.venv/`, `build/`, `dist/`, `*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.opencode/`, `.crush/`, `*.ipsw`, `*.ipsw.lock`, `restore_*.log`, `*.der`, `*.pem`, `*.mobileconfig`, `*.organization`, `Apple Configurator.app/`, `docs/superpowers/`

### 3. Delete Apple Configurator.app/
macOS binary reference — not needed in repo.

### 4. Add LICENSE file
MIT license (as stated in README).

### 5. Update README.md
- Fix CLI name: `apple-device` → `ios-enroll`
- Add missing commands: `org import`, `org export`, `org show`, `org generate`, `org set-mdm-url`, `enroll guided-enroll`, `version`
- Fix org storage path to `~/.config/apple_device_cli/orgs/`
- Note pymobiledevice3 as primary device interaction library

### 6. Update SPEC.md
- Mark enroll.py as removed (legacy)
- Update project structure to reflect src/apple_device_cli/ layout
- Fix org storage path
- Note restore/erase uses pymobiledevice3 (not idevicerestore)
- Remove enroll_gui.py from project structure

### 7. Fix PKCS#7 BER warning
Wrap `pkcs7.load_der_pkcs7_certificates()` in `import_mobileconfig` with `warnings.catch_warnings()` (matching pattern in `_import_from_organization`).

### 8. Delete enroll_gui.py
Broken — references deleted enroll.py and old paths. Gradio not a project dependency.

### 9. Add CHANGELOG.md
Version history stub starting at v0.1.0.

### 10. Tag v0.1.0
After all changes committed, create annotated git tag.
