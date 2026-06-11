# Cleanup Plan — ios-enroll

**Date:** 2026-06-11
**Scope:** Reduce technical debt surfaced in the 2026-06-11 audit
**Approach:** Quick wins first → local refactors → larger structural changes. Each phase is independently shippable.

---

## Phase 0 — Verification baseline (do this first, ~2 min)

Run the full audit one more time so we have a "before" snapshot:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check src/ tests/ --output-format=concise | tail -5
.venv/bin/python -m mypy src/apple_device_cli 2>&1 | tail -3
```

Expected baseline: 167 passed, 4 ruff errors, 12 mypy errors (all pymobiledevice3 import-untyped).

---

## Phase 1 — Trivial lint fixes (5 min, zero risk)

Fix the 4 ruff errors flagged in the audit. Each is a one-liner.

- `tests/test_enrollment_flow_fixes.py:413` — remove `lockdown =` assignment (unused)
- `tests/test_enrollment_flow_fixes.py:490` — same
- `tests/test_enrollment_flow_fixes.py:568` — same
- `tests/test_mobileconfig_import.py:129` — remove unused `import os`

**Verify:** `ruff check src/ tests/` → 0 errors. Run pytest → 167 passed.

---

## Phase 2 — Add lint/typecheck config to pyproject.toml (10 min, zero risk)

Right now `pyproject.toml` has no `[tool.ruff]` or `[tool.mypy]` section, so ruff is using defaults and mypy is complaining about expected pymobiledevice3 stubs. Add a minimal config:

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I"]  # default-ish, but explicit
ignore = [
    "E501",  # line length — handled by formatter/black or future fix
]

[tool.mypy]
python_version = "3.10"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "pymobiledevice3.*"
ignore_missing_imports = true
```

**Why `line-length = 100`:** current code base mixes 88-char and longer lines; 100 is the de-facto modern Python default and matches pymobiledevice3's own style. Reformatting to 88 would be hundreds of churn.

**Why ignore E501 for now:** a future PR can add `ruff format` or `black` and clean these up in one pass. Skipping it now lets us focus on real bugs.

**Verify:** `ruff check` reports 0 errors; `mypy` reports 0 errors.

---

## Phase 3 — Delete unused migration script (5 min, low risk)

`scripts/migrate_orgs.py` (54 lines) is:
- Not imported by any src/ or tests/ code
- Not referenced in README, CHANGELOG, AGENTS, or docs/
- Was authored on the unmerged `feat/enroll-org-migration` branch (which diverges from main by deleting most tests — risky to merge)

**Action:** `git rm scripts/migrate_orgs.py`.

If a real migration is needed later, it can be re-added as a proper CLI subcommand (`ios-enroll org migrate`) with tests.

**Verify:** pytest still 167 passed; ruff still clean.

---

## Phase 4 — Delete stale local branches (1 min, low risk)

Two local branches are not merged into `main`:
- `feat/enroll-org-migration` — diverges heavily (deletes 8,914 lines incl. all tests)
- `feat/mobileconfig-import` — overlap with main is high; the unique commits appear to be re-hashes of work already on main

**Action:**
```bash
git branch -D feat/enroll-org-migration
git branch -D feat/mobileconfig-import
```

**Caveat:** If the user wants to preserve either branch as WIP, skip this phase. Recommend confirming before deletion.

---

## Phase 5 — Refactor `org_set_*` duplication (30 min, medium risk)

The 5 simple field-setters in `src/apple_device_cli/cli.py:758-819` share an identical pattern:

```python
manager = OrganizationManager()
org = manager.get_org(name)
if not org:
    typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
    raise typer.Exit(1)
org.<field> = <value>
manager.save_org(org, overwrite=True)
typer.secho(f"Set <field> for '{_display_name(name)}'", fg=typer.colors.GREEN)
```

**Refactor:** Add a single private helper at the top of cli.py:

```python
def _set_org_field(
    name: str,
    field_name: str,
    value: str,
    label: str,
) -> None:
    """Common body for org set-{cert,key,mdm-url,checkin-url,mdm-topic} commands."""
    manager = OrganizationManager()
    org = manager.get_org(name)
    if not org:
        typer.secho(f"Organization not found: {name}", fg=typer.colors.RED)
        raise typer.Exit(1)
    setattr(org, field_name, value)
    manager.save_org(org, overwrite=True)
    typer.secho(f"Set {label} for '{_display_name(name)}'", fg=typer.colors.GREEN)
```

Then each command becomes ~5 lines:

```python
@org_app.command("set-cert")
def org_set_cert(name: str = typer.Option(..., "--name"),
                 cert: str = typer.Option(..., "-C", "--cert")):
    """Set certificate for organization."""
    _set_org_field(name, "cert_path", str(Path(cert).resolve()), "certificate")
```

Apply to: `set-cert`, `set-key`, `set-mdm-url`, `set-checkin-url`, `set-mdm-topic`.

**Do NOT touch `org_set_wifi`** (`cli.py:900`) — it has unique validation (plist parse, file copy) and doesn't fit the pattern.

**Tests:** No existing CLI-level tests cover these commands (verified — `tests/test_org_set_commands.py` only tests `OrganizationManager` directly). Add 2-3 CliRunner tests to lock in CLI behavior:

```python
def test_set_cert_organization_not_found():
    # typer Exit 1, "Organization not found" in output
def test_set_mdm_url_success():
    # create org, run set-mdm-url, verify field updated
```

**Verify:** pytest passes (167 + 2-3 new); ruff clean; manual smoke test of one command.

**Expected LOC reduction:** ~60 → ~25 in cli.py.

---

## Phase 6 — Consolidate enrollment test fixtures (20 min, low risk)

Both `tests/test_enrollment.py` (9 tests) and `tests/test_enrollment_flow_fixes.py` (20 tests) define a local `mock_pymobiledevice3` autouse fixture with overlapping setup.

**Action:**
1. Move the fixture from `test_enrollment_flow_fixes.py:30-50` into `tests/conftest.py` (current conftest is only 43 lines, mostly empty).
2. Rename the local fixture in `test_enrollment.py` to reuse the conftest one (or delete and rely on conftest autouse).
3. Do NOT merge the two test files — they cover different concerns (basic flow vs. regression fixes). Just share the fixture.

**Verify:** pytest still passes 167.

---

## Phase 7 — Documentation pointer cleanup (10 min, low risk)

`AGENTS.md` "Key Classes & Functions" section hand-types out the `Organization` dataclass fields and `OrganizationManager` methods. This drifts whenever the source changes.

**Action:** Replace the field-by-field listing with a one-line pointer:
> See `src/apple_device_cli/orgs/manager.py:25-50` for the `Organization` dataclass and `:117-155` for `OrganizationManager`'s public API.

Keep the `Skip Panes` section (it's a stable public contract) but trim the field list.

**Verify:** AGENTS.md renders correctly; no test changes.

---

## Phase 8 — Optional: add `make clean` / `scripts/clean.sh` (10 min, low risk)

The working tree accumulates 20GB of `*.ipsw` files and `restore_*.log` files (all gitignored but consuming disk). Add a small script:

```bash
#!/usr/bin/env bash
# Remove IPSW restore files and log artifacts not tracked by git.
set -euo pipefail
rm -f *.ipsw *.ipsw.lock restore_*.log
echo "Cleaned IPSW files and restore logs."
```

Place at `scripts/clean.sh`, `chmod +x`, reference in README's "Development" section.

**Skip if user considers this out of scope.**

---

## Out of scope (intentionally not done)

- **Refactoring `supervised.py` (1051 lines)** — high risk, would need a dedicated design pass. Not "cleanup," it's architectural.
- **Reformatting to 88-char lines** — deferred until a formatter (ruff format or black) is added in a separate PR.
- **Archiving old `docs/superpowers/` plans/specs** — they're not in the way; defer.
- **Removing `.opencode/package.json` and `node_modules/`** — already gitignored; user may have intentional reason to keep them locally.

---

## Execution order & time estimate

| Phase | Time     | Risk     | Independent? |
|-------|----------|----------|--------------|
| 0     | 2 min    | none     | yes          |
| 1     | 5 min    | none     | yes          |
| 2     | 10 min   | none     | yes          |
| 3     | 5 min    | low      | yes          |
| 4     | 1 min    | low      | yes (confirm) |
| 5     | 30 min   | medium   | yes          |
| 6     | 20 min   | low      | yes          |
| 7     | 10 min   | none     | yes          |
| 8     | 10 min   | low      | yes          |

**Total: ~90 min** if all phases run. Each phase ends with `pytest` + `ruff` + `mypy` green.

**Recommended commit strategy:** one commit per phase, using the project's existing format:
```
chore(lint): phase N — <description>
```

---

## Open questions for user before starting

1. **Phase 4 (branch deletion):** OK to delete `feat/enroll-org-migration` and `feat/mobileconfig-import`? Both are unmerged and divergent.
2. **Phase 3 (migrate_orgs):** OK to delete the script, or should it be promoted to a CLI subcommand with tests?
3. **Phase 2 (ruff line-length):** Confirm 100 is acceptable, or prefer 88 (forces a reformat pass) or 120 (looser)?
