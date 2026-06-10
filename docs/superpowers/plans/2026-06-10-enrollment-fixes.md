# Enrollment Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land 6 real fixes and 3 doc-only notes about 9 candidate issues found during a 1.0.0b code review, plus 1 pre-work gitignore cleanup — 10 commits, 1 PR.

**Architecture:** One commit per issue. Doc-only commits (1-3) ship first to ground-truth the analysis. Pure renames/dedents (4, 7) and dead-code removal (5) come next. The two highest-risk changes — `fcntl.flock` race-fix (6) and keybag cleanup-on-exception (8) — ship later. Re-enroll exit code fix (9) is the last code-changing commit.

**Tech Stack:** Python 3.10+, Typer, pymobiledevice3, cryptography, `pytest` with `unittest.mock` patches.

---

## File Structure

| File | Change | Why |
|------|--------|-----|
| `.gitignore` | Modify (pre-work) | Unignore `docs/superpowers/specs/` and `docs/superpowers/plans/` |
| `AGENTS.md` | Modify (issues 1-3) | Document the 3 false-alarm concerns |
| `src/apple_device_cli/enrollment/supervised.py` | Modify (issues 4, 7, 8) | Rename field, dedent block, add try/finally |
| `src/apple_device_cli/cli.py` | Modify (issues 4, 9) | Update field consumer, add `typer.Exit(1)` |
| `src/apple_device_cli/orgs/manager.py` | Modify (issue 6) | Add `_acquire_org_lock` helper, wrap 2 sites |
| `src/apple_device_cli/enrollment/flows.py` | Delete (issue 5) | Dead code |
| `tests/test_enrollment_flow_fixes.py` | Modify (issues 4, 8, 9) | Update 1 line, add 2 new tests |
| `tests/test_org_manager.py` | Modify (issue 6) | Add 2 new lock tests |
| `tests/test_enrollment_flows.py` | Delete (issue 5) | Tests for deleted code |
| `tests/test_enrollment_integration.py` | Delete (issue 5) | Tests for deleted code |

No new files. No new modules. All changes live in the existing 4 source files and 3 test files.

---

## Task 0: Pre-work — `chore(gitignore): unignore docs/superpowers/specs and plans`

**Files:**
- Modify: `.gitignore` (remove line 62 and its preceding comment)

- [ ] **Step 1: Verify current state and confirm 11 tracked files exist under `docs/superpowers/`**

Run: `git ls-files docs/superpowers/ | wc -l`
Expected: `11`

- [ ] **Step 2: Remove the overly broad gitignore rule**

Edit `.gitignore`. Replace this block:

```gitignore
# Internal dev docs (not for release)
docs/superpowers/
```

With: (nothing — delete both lines)

- [ ] **Step 3: Verify no other content was being ignored**

Run: `ls -la docs/superpowers/`
Expected: only `plans/` and `specs/` subdirectories visible.

Run: `git status`
Expected: `.gitignore` is the only modified file.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore(gitignore): unignore docs/superpowers/specs and plans

The docs/superpowers/ rule was added before specs/ and plans/ existed
and excludes content that is already tracked (11 files). Specs and
plans are clearly meant to be tracked for project history; remove the
broad rule. If a future subdir needs ignoring, add a more specific rule."
```

Expected: commit created, working tree clean.

---

## Task 1: `docs(enrollment): document activation state check`

**Files:**
- Modify: `AGENTS.md` (append a new subsection under "Critical Behaviors & Gotchas")

- [ ] **Step 1: Open `AGENTS.md` and locate the "Critical Behaviors & Gotchas" section**

Run: `grep -n "Critical Behaviors & Gotchas" AGENTS.md`
Expected: a line number around 200-210.

- [ ] **Step 2: Append the new subsection**

Add this text as the **last subsection** in "Critical Behaviors & Gotchas" (right before the `---` separator that precedes "External Dependencies"):

```markdown
### Activation State String Comparison

The check `if activation_state == "Unactivated":` at
`enrollment/supervised.py:525` uses a string literal that *looks* brittle
(magic string, no constant), but it matches pymobiledevice3's own
convention. `MobileActivationService.activate()` itself opens with
`if await self.state() != "Unactivated": return`
(`pymobiledevice3/services/mobile_activation.py:105`).

The string `"Unactivated"` is not in pymobiledevice3's public API, so
this cannot be replaced with a named constant without a local re-export.
Treat it as a stable convention shared with the library.
```

- [ ] **Step 3: Verify the diff reads cleanly**

Run: `git diff AGENTS.md`
Expected: only the new subsection is added, no other content disturbed.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(enrollment): document activation state check

Issue 1 from the 2026-06-10 enrollment review. The string literal
'Unactivated' at supervised.py:525 looks like a magic string but
matches pymobiledevice3's own MobileActivationService.activate()
idiom. Document why the literal is correct so future maintainers do
not 'fix' it with a local constant."
```

Expected: commit created, no other files modified.

---

## Task 2: `docs(skip-panes): document apple-pay → Payment mapping`

**Files:**
- Modify: `AGENTS.md` (append a new subsection under "Critical Behaviors & Gotchas")

- [ ] **Step 1: Append the new subsection**

Add this text as the **last subsection** in "Critical Behaviors & Gotchas", right after the one added in Task 1:

```markdown
### Skip Pane Mapping Exceptions

`SKIP_SETUP_MAPPING` in `enrollment/supervised.py:39` maps user-facing
pane names to lockdown key values. Most look obvious (`"location"` →
`"Location"`), but a few are intentionally non-obvious:

- `"apple-pay"` → `"Payment"` — the iOS 8.1+ lockdown key for "Skips
  Apple Pay setup" is named `Payment`, not `ApplePay`. Source: Apple's
  [`device-management/skipkeys.yaml`](https://github.com/apple/device-management/blob/main/skipkeys.yaml).

All 62 entries were cross-checked against pymobiledevice3's
`MobileConfigService.supervise()` default skip list
(`services/mobile_config.py:201-283`). When adding new entries, consult
that list as the source of truth.
```

- [ ] **Step 2: Verify the diff**

Run: `git diff AGENTS.md`
Expected: the new subsection is appended without disturbing the previous one.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(skip-panes): document apple-pay → Payment mapping

Issue 2 from the 2026-06-10 enrollment review. The mapping
'apple-pay' → 'Payment' looks wrong (Apple Pay ≠ Payment) but is
correct per Apple's skipkeys.yaml. Document the unusual mapping and
point future maintainers at the pymobiledevice3 reference list."
```

Expected: commit created.

---

## Task 3: `docs(enrollment): note flows.py decision`

**Files:**
- Modify: `AGENTS.md` (add a one-line note in the "Architecture" section)

- [ ] **Step 1: Locate the "Architecture" diagram**

Run: `grep -n "## Architecture" AGENTS.md`
Expected: a line number near the top (around 35).

- [ ] **Step 2: Add the note after the existing flow description**

The Architecture section contains a fenced block:

```dot
User Input (CLI)
    |
    v
Typer App (cli.py)
    |
    +-- device/      # Device enumeration, info
    +-- enrollment/  # Supervised pairing, activation, skip panes
    +-- orgs/        # Organization management, identity
    +-- core/        # Exceptions, redaction utilities
```

Replace it with this version (one new trailing line):

```dot
User Input (CLI)
    |
    v
Typer App (cli.py)
    |
    +-- device/      # Device enumeration, info
    +-- enrollment/  # Supervised pairing, activation, skip panes
    +-- orgs/        # Organization management, identity
    +-- core/        # Exceptions, redaction utilities

Note: `enrollment/flows.py` was removed in 1.0.0b-post as dead code
(not imported by cli.py or any other production module).
```

- [ ] **Step 3: Verify the diff**

Run: `git diff AGENTS.md`
Expected: the fenced diagram is unchanged, and the trailing `Note:` paragraph is added.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(enrollment): note flows.py decision

Issue 3 from the 2026-06-10 enrollment review. Note in the
Architecture section that enrollment/flows.py is intentionally absent
so future maintainers do not re-introduce the unused abstraction."
```

Expected: commit created.

---

## Task 4: `fix(state): rename is_mdm_managed → was_mandatorily_unpaired`

**Files:**
- Modify: `src/apple_device_cli/enrollment/supervised.py:1001,1015,1039`
- Modify: `src/apple_device_cli/cli.py:1180`
- Modify: `tests/test_enrollment_flow_fixes.py:386`

This is a pure rename — 5 sites total. No new code, no new behavior. Existing tests still pass after the rename.

- [ ] **Step 1: Rename in `supervised.py`**

Edit `src/apple_device_cli/enrollment/supervised.py`. Make 3 replacements:

- Line 1001: replace `is_mdm_managed = await _get_lockdown_value(lockdown, "WasMandatorilyUnpaired")` with `was_mandatorily_unpaired = await _get_lockdown_value(lockdown, "WasMandatorilyUnpaired")`
- Line 1015: replace `"is_mdm_managed": bool(is_mdm_managed),` with `"was_mandatorily_unpaired": bool(was_mandatorily_unpaired),`
- Line 1039: replace `"is_mdm_managed": False,` with `"was_mandatorily_unpaired": False,`

- [ ] **Step 2: Rename in `cli.py`**

Edit `src/apple_device_cli/cli.py`. Replace line 1180:

```python
        typer.echo(f"  MDM Managed: {state.get('is_mdm_managed', False)}")
```

with:

```python
        typer.echo(f"  Was Mandatorily Unpaired: {state.get('was_mandatorily_unpaired', False)}")
```

(Updating the display label is part of the fix — the old label was wrong. The new label is honest about what the lockdown key actually means.)

- [ ] **Step 3: Update the test**

Edit `tests/test_enrollment_flow_fixes.py`. Replace line 386:

```python
            "is_mdm_managed": False,
```

with:

```python
            "was_mandatorily_unpaired": False,
```

- [ ] **Step 4: Verify the test suite passes**

Run: `PYTHONPATH=src python -m pytest tests/test_enrollment_flow_fixes.py -v`
Expected: all tests pass.

- [ ] **Step 5: Search for any remaining occurrences**

Run: `grep -rn "is_mdm_managed" src/ tests/`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/apple_device_cli/enrollment/supervised.py \
        src/apple_device_cli/cli.py \
        tests/test_enrollment_flow_fixes.py
git commit -m "fix(state): rename is_mdm_managed → was_mandatorily_unpaired

Issue 4 from the 2026-06-10 enrollment review. The dict key was
populated from the WasMandatorilyUnpaired lockdown value but stored
under the misleading name is_mdm_managed. There is no IsMDMEnrolled
lockdown key in pymobiledevice3; MDM enrollment status is derived
from IsSupervised + installed profile enumeration. Rename the local
key to be honest about what it actually contains, and update the CLI
display label from 'MDM Managed' to 'Was Mandatorily Unpaired' to
match.

Touches:
- supervised.py: 3 sites (read, return dict, MissingValueError fallback)
- cli.py: 1 site (display label and dict lookup)
- test_enrollment_flow_fixes.py: 1 site (expected return dict)"
```

Expected: commit created.

---

## Task 5: `chore(enrollment): delete unused flows.py`

**Files:**
- Delete: `src/apple_device_cli/enrollment/flows.py`
- Delete: `tests/test_enrollment_flows.py`
- Delete: `tests/test_enrollment_integration.py`

No new code, no new tests. Verification: the full suite must still pass (fewer tests, no failures).

- [ ] **Step 1: Verify the three files are unused outside their test siblings**

Run: `grep -rn "from apple_device_cli.enrollment.flows\|import.*\\.flows" src/`
Expected: no output.

Run: `grep -rln "SimpleSupervisedEnrollment\|ReenrollmentFlow\|FlowRegistry" src/`
Expected: no output (only the 2 test files reference these names).

- [ ] **Step 2: Delete the three files**

```bash
git rm src/apple_device_cli/enrollment/flows.py \
       tests/test_enrollment_flows.py \
       tests/test_enrollment_integration.py
```

- [ ] **Step 3: Run the full test suite**

Run: `PYTHONPATH=src python -m pytest tests/ -v`
Expected: all remaining tests pass; 3 fewer test files in the output.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(enrollment): delete unused flows.py

Issue 5 from the 2026-06-10 enrollment review. SimpleSupervisedEnrollment,
ReenrollmentFlow, and FlowRegistry in enrollment/flows.py are dead code —
not imported by cli.py or any other production module, only by the two
deleted test files. Total: 646 lines removed.

Future maintainers: do not re-introduce this abstraction. AGENTS.md
Architecture section notes the decision."
```

Expected: commit created.

---

## Task 6: `fix(orgs): add fcntl.flock around save_org and import_mobileconfig`

**Files:**
- Modify: `src/apple_device_cli/orgs/manager.py` (new helper, wrap 2 sites)
- Modify: `tests/test_org_manager.py` (add 2 new tests)

This is the highest-risk code change. Two concurrent `ios-enroll org create` invocations can both pass the existence check and both proceed to write to the same `orgs_dir/<name>/` directory, corrupting state. `fcntl.flock(LOCK_EX)` on a per-org lock file is the fix.

- [ ] **Step 1: Add `fcntl` import**

Edit `src/apple_device_cli/orgs/manager.py`. Add `import fcntl` to the stdlib import block at the top of the file. The current block is:

```python
from __future__ import annotations

import base64
import json
import plistlib
import logging
import shutil
import subprocess
import tempfile
import warnings
```

Change it to:

```python
from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import plistlib
import logging
import shutil
import subprocess
import tempfile
import warnings
```

(`contextlib` is needed for the lock context manager.)

- [ ] **Step 2: Add the `_acquire_org_lock` helper method**

Edit `src/apple_device_cli/orgs/manager.py`. Add this method to the `OrganizationManager` class. Place it directly after `_sanitize_name` (line 151-152) and before `import_org` (line 154):

```python
    @contextlib.contextmanager
    def _acquire_org_lock(self, name: str):
        """Acquire an exclusive cross-process lock on a per-org lock file.

        Prevents races between concurrent save_org and import_mobileconfig
        calls for the same org name. Auto-releases on context exit (even
        on exception). Uses fcntl.flock so multiple processes coordinate
        correctly, not just multiple threads.
        """
        lock_path = self.orgs_dir / f".{self._sanitize_name(name)}.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
```

- [ ] **Step 3: Add `os` import**

The helper uses `os.open` and `os.close`. Edit the stdlib import block again to add `import os` (alphabetical order: between `json` and `plistlib`):

```python
import base64
import contextlib
import fcntl
import json
import logging
import os
import plistlib
import shutil
import subprocess
import tempfile
import warnings
```

- [ ] **Step 4: Wrap `save_org` in the lock**

Edit `src/apple_device_cli/orgs/manager.py`. Replace the `save_org` method (lines 138-142):

```python
    def save_org(self, org: Organization, overwrite: bool = False):
        org_dir = self.orgs_dir / self._sanitize_name(org.name)
        if not overwrite and org_dir.exists():
            raise ValueError(f"Organization '{org.name}' already exists")
        org.save(org_dir)
```

with:

```python
    def save_org(self, org: Organization, overwrite: bool = False):
        with self._acquire_org_lock(org.name):
            org_dir = self.orgs_dir / self._sanitize_name(org.name)
            if not overwrite and org_dir.exists():
                raise ValueError(f"Organization '{org.name}' already exists")
            org.save(org_dir)
```

- [ ] **Step 5: Wrap the filesystem portion of `import_mobileconfig` in the lock**

Edit `src/apple_device_cli/orgs/manager.py`. The `import_mobileconfig` method (lines 271-356) has filesystem-touching work in its second half. Wrap the section starting at line 310 (`existing_org = self.get_org(name)`) through the end of the method (line 356, `return org`).

Replace lines 310-356 with:

```python
        existing_org = self.get_org(name)
        if existing_org:
            raise ValueError(f"Organization '{name}' already exists")

        with self._acquire_org_lock(name):
            mdm_url = None
            checkin_url = None
            mdm_topic = None
            identity_ref = None
            for item in payload.get('PayloadContent', []):
                if isinstance(item, dict) and item.get('PayloadType') == 'com.apple.mdm':
                    mdm_url = item.get('ServerURL')
                    checkin_url = item.get('CheckInURL')
                    mdm_topic = item.get('Topic')
                    identity_ref = item.get('IdentityCertificateUUID')
                    break

            dest_dir = self.orgs_dir / self._sanitize_name(name)
            dest_dir.mkdir(parents=True, exist_ok=True)

            # Always generate supervision identity - PKCS7 certs are server/CA certs, not client identity
            from apple_device_cli.orgs.identity import generate_org_identity

            cert_der, key_der = generate_org_identity(name)
            with open(dest_dir / "cert.der", "wb") as f:
                f.write(cert_der)
            with open(dest_dir / "key.der", "wb") as f:
                f.write(key_der)

            org = Organization(
                name=name,
                org_id=mdm_topic,
                mdm_url=mdm_url,
                checkin_url=checkin_url,
                mdm_topic=mdm_topic,
                identity_ref=identity_ref,
                mdm_description=payload.get('PayloadDescription'),
                cert_path=str(dest_dir / "cert.der"),
                key_path=str(dest_dir / "key.der"),
                wifi_config_path=str(dest_dir / "wifi.mobileconfig"),
            )

            # Save mobileconfig file for MDM enrollment
            with open(dest_dir / "mdm.mobileconfig", "wb") as f:
                f.write(result.stdout)

            org.save(org_dir=dest_dir, skip_copy=True)
            return org
```

(The early `existing_org` check stays outside the lock to keep the fast-fail path lock-free. The duplicate check inside the lock is not needed — `save_org` will still raise if a race creates the dir between the two checks, and the lock prevents that race.)

- [ ] **Step 6: Add the first new test — `test_save_org_acquires_lock`**

Append to `tests/test_org_manager.py`:

```python
def test_save_org_acquires_fcntl_lock():
    """save_org must hold an fcntl.flock on a per-org lock file for the duration."""
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))
        org = Organization(name="Test Org", org_id="com.test")

        with patch("apple_device_cli.orgs.manager.fcntl.flock") as mock_flock, \
             patch("apple_device_cli.orgs.manager.os.open", return_value=42) as mock_open, \
             patch("apple_device_cli.orgs.manager.os.close") as mock_close:
            manager.save_org(org)

        # os.open must be called with O_CREAT on a per-org lock path
        assert mock_open.called
        lock_path_arg = mock_open.call_args[0][0]
        assert str(lock_path_arg).endswith(".Test_Org.lock")
        assert lock_path_arg.name.startswith(".")

        # flock must be called with LOCK_EX first (acquire) and LOCK_UN last (release)
        flock_calls = mock_flock.call_args_list
        assert len(flock_calls) >= 2
        assert flock_calls[0].args[1] == fcntl.LOCK_EX
        assert any(c.args[1] == fcntl.LOCK_UN for c in flock_calls)

        # fd must be closed
        mock_close.assert_called_once_with(42)

        # Org was actually saved
        assert (manager.orgs_dir / "Test_Org" / "org.json").exists()
```

This test requires `fcntl` and `patch` imports at the top of the test file. Add to the existing import block:

```python
import fcntl
from unittest.mock import patch
```

(The `from unittest.mock import patch` goes alongside the existing `import` lines near the top of the file.)

- [ ] **Step 7: Add the second new test — `test_concurrent_save_org_race`**

Append to `tests/test_org_manager.py`:

```python
def test_concurrent_save_org_serialized_by_lock():
    """Two threads calling save_org for the same org must serialize — no corruption."""
    import threading

    with tempfile.TemporaryDirectory() as tmpdir:
        manager = OrganizationManager(Path(tmpdir))

        # Pre-acquire the lock file from the test thread so the second
        # save_org call will block on fcntl.flock(LOCK_EX) until we release.
        lock_path = manager.orgs_dir / ".Test_Org.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)

        results = {}
        barrier = threading.Barrier(2)

        def attempt_save():
            try:
                org = Organization(name="Test Org", org_id="com.test")
                barrier.wait()
                manager.save_org(org)
                results["ok"] = True
            except Exception as e:
                results["err"] = e

        t = threading.Thread(target=attempt_save)
        t.start()
        # Give the thread time to enter flock and block
        t.join(timeout=0.5)
        assert not results, "second save_org should block while first holds the lock"

        # Release the lock; second save should now complete
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        t.join(timeout=5)
        assert results.get("ok") is True
        assert (manager.orgs_dir / "Test_Org" / "org.json").exists()
```

This test needs `import os` at the top of the test file as well.

- [ ] **Step 8: Run the new tests**

Run: `PYTHONPATH=src python -m pytest tests/test_org_manager.py -v -k "lock"`
Expected: both new tests pass.

- [ ] **Step 9: Run the full suite**

Run: `PYTHONPATH=src python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 10: Lint and type-check**

Run: `ruff check src/ tests/`
Expected: no errors.

Run: `mypy src/`
Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add src/apple_device_cli/orgs/manager.py tests/test_org_manager.py
git commit -m "fix(orgs): add fcntl.flock around save_org and import_mobileconfig

Issue 6 from the 2026-06-10 enrollment review. Two concurrent
ios-enroll invocations (e.g. 'org create' racing with 'org import',
or two parallel 'org create' for the same name) can both pass the
existence check and both write to orgs_dir/<name>/, corrupting state.

Fix: wrap the filesystem portion of save_org and import_mobileconfig
in a per-org fcntl.flock(LOCK_EX). Lock auto-releases on exception
or normal exit. Chose fcntl.flock over threading.Lock (cross-process),
filelock (third-party), and os.open(O_CREAT|O_EXCL) (single-file only,
not multi-file writes)."
```

Expected: commit created.

---

## Task 7: `chore(enrollment): remove empty TemporaryDirectory`

**Files:**
- Modify: `src/apple_device_cli/enrollment/supervised.py` (dedent lines 541-611, drop the misleading line-534 comment)

The `with tempfile.TemporaryDirectory():` block in `do_supervised_pairing` never uses the tempdir. The block builds a dict, calls pymobiledevice3 services, and never touches the filesystem. The comment on line 534 exists only to explain why the keybag lives outside the (unused) tempdir.

- [ ] **Step 1: Verify the tempdir is unused inside the block**

Run: `awk 'NR>=541 && NR<=611' src/apple_device_cli/enrollment/supervised.py | grep -E "tmpdir|tempdir|TemporaryDirectory"`
Expected: no output (the tempdir name from `as tempfile.TemporaryDirectory() as tmpdir` is never used).

(The current `with` statement is `with tempfile.TemporaryDirectory():` — no `as` binding — confirming the tempdir is discarded.)

- [ ] **Step 2: Delete the misleading comment**

Edit `src/apple_device_cli/enrollment/supervised.py`. Delete line 534 in its entirety (the comment "Create keybag BEFORE tempdir block - needed for MDM install after tempdir closes").

After deletion, line 534 (the `keybag_path = ...` line) becomes the new comment-anchor.

- [ ] **Step 3: Dedent the `with` block**

Edit `src/apple_device_cli/enrollment/supervised.py`. The block at lines 541-611 currently has 8 extra spaces of indentation due to the `with` wrapper. Dedent it by 4 spaces (one level) — the lines inside were indented for the `with` block, and after removal they should sit at the level of the surrounding code in `do_supervised_pairing`.

The current shape (abbreviated):

```python
    if Path(cert_path).exists() and Path(key_path).exists():
        _create_keybag_file_from_identity(keybag_path, cert_path, key_path)
    else:
        create_keybag_file(keybag_path, org_name)

    with tempfile.TemporaryDirectory():
        # Build cloud configuration payload
        cloud_config_payload = {
            ...
        }
        ...
        # Apply cloud configuration
        try:
            async with MobileConfigService(lockdown) as svc:
                ...
        except Exception as e:
            if isinstance(e, CloudConfigurationAlreadyPresentError):
                ...
```

Replace with:

```python
    if Path(cert_path).exists() and Path(key_path).exists():
        _create_keybag_file_from_identity(keybag_path, cert_path, key_path)
    else:
        create_keybag_file(keybag_path, org_name)

    # Build cloud configuration payload
    cloud_config_payload = {
        ...
    }
    ...
    # Apply cloud configuration
    try:
        async with MobileConfigService(lockdown) as svc:
            ...
    except Exception as e:
        if isinstance(e, CloudConfigurationAlreadyPresentError):
            ...
```

The full dedented body (8 spaces of outer indent + 4 more for the `with` block) becomes 8 spaces of outer indent.

- [ ] **Step 4: Verify the `tempfile` import is still needed**

Run: `grep -n "tempfile" src/apple_device_cli/enrollment/supervised.py`
Expected: at least 1 other use (the keybag creation at line 535 uses `tempfile.gettempdir()`).

- [ ] **Step 5: Run the test suite**

Run: `PYTHONPATH=src python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/apple_device_cli/enrollment/supervised.py
git commit -m "chore(enrollment): remove empty TemporaryDirectory

Issue 7 from the 2026-06-10 enrollment review. The
'with tempfile.TemporaryDirectory():' wrapper in do_supervised_pairing
never used the tempdir — the block builds a dict and calls
pymobiledevice3 services, none of which touch the filesystem. The
accompanying 'Create keybag BEFORE tempdir block' comment existed
only to explain why the keybag lives outside the (unused) tempdir.

Dedent the block, drop the wrapper and the comment. Keybag lifetime
is unchanged. tempfile import stays (still used for keybag path)."
```

Expected: commit created.

---

## Task 8: `fix(enrollment): ensure keybag cleanup on exception`

**Files:**
- Modify: `src/apple_device_cli/enrollment/supervised.py` (extract helper, wrap in try/finally)
- Modify: `tests/test_enrollment_flow_fixes.py` (add `test_keybag_cleaned_up_on_exception`)

The keybag file in `/tmp` contains PEM-formatted supervision private key + certificate. The existing cleanup at lines 751-755 runs only on the success path. If anything in the 200+ lines between keybag creation (line 535) and cleanup (line 751) raises, the file leaks sensitive material.

- [ ] **Step 1: Add the `_cleanup_keybag` helper**

Edit `src/apple_device_cli/enrollment/supervised.py`. Find a good location for a module-level helper (near the other private helpers, e.g. just above `do_supervised_pairing` around line 423). Insert:

```python
def _cleanup_keybag(keybag_path: Path | None) -> None:
    """Remove a keybag file, swallowing OSError.

    The keybag contains sensitive supervision identity material and
    should be deleted as soon as MDM installation is complete. Cleanup
    failures are non-fatal — we log a warning but do not raise, so a
    transient filesystem error during cleanup cannot mask a successful
    enrollment.
    """
    if keybag_path and keybag_path.exists():
        try:
            keybag_path.unlink()
        except OSError as cleanup_err:
            _logger.warning("could not clean up keybag file %s: %s", keybag_path, cleanup_err)
```

(`_logger` is already defined at the top of the module.)

- [ ] **Step 2: Wrap the keybag-protected portion of `do_supervised_pairing` in try/finally**

The keybag is created at lines 535-539. The existing cleanup is at lines 750-755. Replace those two regions with the new structure below. The body in between (lines 541-749, ~200 lines) is left unchanged.

Edit `src/apple_device_cli/enrollment/supervised.py`. Replace lines 534-539 (the keybag-creation block — comment + 2-line `if`/`else`):

```python
    # Create keybag BEFORE tempdir block - needed for MDM install after tempdir closes
    keybag_path = Path(tempfile.gettempdir()) / f"ios_enroll_keybag_{uuid4().hex[:8]}"
    if Path(cert_path).exists() and Path(key_path).exists():
        _create_keybag_file_from_identity(keybag_path, cert_path, key_path)
    else:
        create_keybag_file(keybag_path, org_name)
```

with:

```python
    keybag_path = Path(tempfile.gettempdir()) / f"ios_enroll_keybag_{uuid4().hex[:8]}"
    if Path(cert_path).exists() and Path(key_path).exists():
        _create_keybag_file_from_identity(keybag_path, cert_path, key_path)
    else:
        create_keybag_file(keybag_path, org_name)

    try:
```

(Adds 4-space indent + a blank line, then opens a `try:` block. The `keybag_path` line is now the last thing executed *before* the `try:` opens.)

Then replace the existing cleanup block at lines 750-755:

```python
    # Clean up keybag file - contains sensitive certificate material
    if keybag_path and keybag_path.exists():
        try:
            keybag_path.unlink()
        except OSError as cleanup_err:
            _progress(f"Warning: could not clean up keybag file: {cleanup_err}")
```

with:

```python
    finally:
        _cleanup_keybag(keybag_path)
```

(Replaces 6 lines of inline cleanup with 2 lines of `finally` + helper call.)

The `return EnrollmentResult(...)` at line 757 is unchanged. All body code between the new `try:` and the new `finally:` is unchanged.

- [ ] **Step 3: Verify the new structure is correct**

Run: `grep -n "_cleanup_keybag\|^    try:\|^    finally:" src/apple_device_cli/enrollment/supervised.py`
Expected: 1 occurrence of `_cleanup_keybag(keybag_path)` inside a `finally:` clause, 1 occurrence of `_cleanup_keybag` as a definition.

- [ ] **Step 4: Add the new test**

Append to `tests/test_enrollment_flow_fixes.py`. The test simulates an exception during `set_cloud_configuration` and asserts the keybag file is removed. (`MagicMock`, `AsyncMock`, and `patch` are already imported at the top of this file.)

```python
class TestKeybagCleanupOnException:
    """Verify the keybag is cleaned up even when the enrollment flow raises."""

    def test_keybag_cleaned_up_when_set_cloud_configuration_fails(
        self, mock_pymobiledevice3, tmp_path
    ):
        from apple_device_cli.enrollment import supervised

        # Build a fake MobileConfigService whose set_cloud_configuration raises
        async def boom(*args, **kwargs):
            raise RuntimeError("simulated supervision failure")

        svc = MagicMock()
        svc.set_cloud_configuration = boom
        svc.__aenter__ = AsyncMock(return_value=svc)
        svc.__aexit__ = AsyncMock(return_value=False)

        lockdown = MagicMock()
        lockdown.udid = "test-udid"
        lockdown.get_value = AsyncMock(return_value="Activated")

        with patch("pymobiledevice3.services.mobile_config.MobileConfigService", return_value=svc), \
             patch.object(supervised, "create_using_usbmux", new=AsyncMock(return_value=lockdown)), \
             patch.object(supervised, "create_keybag_file") as mock_keybag, \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.unlink") as mock_unlink:
            # Make create_keybag_file create our fake keybag file on disk
            def make_fake(path, *_args, **_kwargs):
                Path(path).write_text("fake-cert-material")
            mock_keybag.side_effect = make_fake

            with pytest.raises(RuntimeError, match="simulated supervision failure"):
                supervised.do_supervised_pairing(
                    cert_path="/tmp/cert",
                    key_path="/tmp/key",
                    org_name="Test Org",
                )

        # The finally block must have called unlink on the keybag path
        assert mock_unlink.called, "keybag should be unlinked even when enrollment raises"
```

- [ ] **Step 5: Run the new test**

Run: `PYTHONPATH=src python -m pytest tests/test_enrollment_flow_fixes.py::TestKeybagCleanupOnException -v`
Expected: 1 test passes.

- [ ] **Step 6: Run the full suite**

Run: `PYTHONPATH=src python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 7: Lint and type-check**

Run: `ruff check src/ tests/ && mypy src/`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/apple_device_cli/enrollment/supervised.py \
        tests/test_enrollment_flow_fixes.py
git commit -m "fix(enrollment): ensure keybag cleanup on exception

Issue 8 from the 2026-06-10 enrollment review. The keybag file
in /tmp/ios_enroll_keybag_<hex> contains PEM-formatted supervision
private key + certificate. The existing cleanup ran only on the
success path (between Step 6 verify and the return). If anything
in the 200+ lines between keybag creation and cleanup raised
(activation, config apply, WiFi install, MDM install, verify), the
file leaked sensitive material.

Fix: extract cleanup into _cleanup_keybag(keybag_path) helper with
the same try/except OSError semantics (now via _logger.warning);
wrap do_supervised_pairing body in try/finally. Success path
behavior is unchanged (cleanup still runs, still swallows OSError)."
```

Expected: commit created.

---

## Task 9: `fix(cli): print error and exit non-zero on re-enroll failure`

**Files:**
- Modify: `src/apple_device_cli/cli.py:1140-1145` (add `raise typer.Exit(1)`)
- Modify: `tests/test_enrollment_flow_fixes.py` (add `test_reenroll_exits_nonzero_on_error`)

The `enroll_reenroll` command catches `AppleDeviceError`, prints in red, but does not call `raise typer.Exit(1)`. Scripts that wrap `ios-enroll enroll re-enroll` see exit 0 even on failure.

- [ ] **Step 1: Add `raise typer.Exit(1)` after the error print**

Edit `src/apple_device_cli/cli.py`. Replace the `except AppleDeviceError` block (lines 1144-1145):

```python
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
```

with:

```python
    except AppleDeviceError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(1)
```

- [ ] **Step 2: Add the new test**

Append to `tests/test_enrollment_flow_fixes.py`:

```python
class TestReenrollExitCode:
    """Verify ios-enroll enroll re-enroll exits non-zero on failure."""

    def test_reenroll_exits_nonzero_on_apple_device_error(
        self, mock_pymobiledevice3, tmp_path
    ):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from apple_device_cli.cli import enroll_app
        from apple_device_cli.core.exceptions import AppleDeviceError

        fake_device = MagicMock()
        fake_device.udid = "test-udid"
        fake_device.device_name = "Test iPad"

        runner = CliRunner()
        with patch("apple_device_cli.cli._prompt_for_udid", return_value=fake_device), \
             patch(
                 "apple_device_cli.cli.erase_device_for_reenrollment",
                 side_effect=AppleDeviceError("erase failed"),
             ):
            result = runner.invoke(enroll_app, ["re-enroll", "--udid", "test-udid", "--force"])

        assert result.exit_code == 1
        assert "Error" in result.stdout or "erase failed" in result.stdout
```

The test needs `CliRunner` and `AppleDeviceError` imports at the top of the test file. Add to the existing import block (lines 1-11):

```python
from typer.testing import CliRunner
from apple_device_cli.core.exceptions import AppleDeviceError
```

(`MagicMock` and `patch` are already imported.)

- [ ] **Step 3: Run the new test**

Run: `PYTHONPATH=src python -m pytest tests/test_enrollment_flow_fixes.py::TestReenrollExitCode -v`
Expected: 1 test passes.

- [ ] **Step 4: Run the full suite**

Run: `PYTHONPATH=src python -m pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 5: Lint, format, type-check**

Run: `ruff check src/ tests/ && ruff format --check && mypy src/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/apple_device_cli/cli.py tests/test_enrollment_flow_fixes.py
git commit -m "fix(cli): print error and exit non-zero on re-enroll failure

Issue 9 from the 2026-06-10 enrollment review. enroll_reenroll
caught AppleDeviceError, printed in red, but did not raise
typer.Exit(1). The command exited 0 even on failure, so scripts
that wrap 'ios-enroll enroll re-enroll' saw a successful exit
code and proceeded to treat the device as ready for re-enrollment
when it was not.

Add raise typer.Exit(1) after the error print. Mirrors the pattern
in enroll_status and other commands."
```

Expected: commit created. Working tree clean. 10 commits ahead of `origin/main`.

---

## Cross-cutting verification

After Task 9, before opening the PR, run the full verification suite:

- [ ] `PYTHONPATH=src python -m pytest tests/ -v` — all green
- [ ] `ruff check src/ tests/` — clean
- [ ] `ruff format --check` — clean
- [ ] `mypy src/` — clean
- [ ] `git log --oneline origin/main..HEAD` — 10 commits present
- [ ] `git status` — clean working tree

If any check fails, fix it in a `chore:` or `fix:` commit on top — do not amend prior commits (preserves bisectability).
