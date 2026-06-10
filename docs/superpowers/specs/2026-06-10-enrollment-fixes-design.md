# Enrollment Fixes Design

**Date:** 2026-06-10
**Target:** ios-enroll post-1.0.0b
**Scope:** 9 enrollment issues — 6 real fixes, 3 docs-only notes about false-alarm concerns
**Deliverable:** 10 commits, 1 PR (1 pre-work chore + 9 issue commits)

## Context

A deep dive into `src/apple_device_cli/enrollment/` surfaced 9 candidate issues
during a code review of the 1.0.0b release. One pre-work chore (tightening
`.gitignore`, which previously excluded all of `docs/superpowers/` even though
11 specs/plans are tracked there) ships as commit 0. After that, the 9 issues:

- 6 are real bugs or dead code that need fixing
- 3 are false alarms where the code is correct but *looks* wrong, and need
  documentation to prevent future re-investigation

Each issue gets its own commit. The 3 doc-only commits come first to
ground-truth the analysis before any code changes.

## Decisions

- **One commit per issue** — preserves bisectability, makes review atomic, and
  lets any single fix be cherry-picked or reverted independently.
- **Doc-only commits ship first** (issues 1, 2, 3) — they document why the
  existing code is correct, so reviewers of the later code-changing commits
  have the context.
- **Dead-code removal before real fixes** (issue 5) — shrinks the file tree
  before touching the more delicate enrollment logic.
- **Internal rename before behavior change** (issue 4) — easy to revert, no
  external API change.
- **Keybag cleanup is the last code-changing commit** (issue 8) — it's the
  highest-risk change (touches error paths), and shipping it last means the
  surrounding code is already stable.

## Changes

### Pre-work — `chore(gitignore): unignore docs/superpowers/specs and plans`

`.gitignore` line 62 (`docs/superpowers/`) was added before specs and plans
were committed; it required `git add -f` for this spec to be tracked. 11
files in `docs/superpowers/specs/` and `docs/superpowers/plans/` are
already tracked. No other content exists under `docs/superpowers/`.

**Change:** Remove the `docs/superpowers/` line. The "Internal dev docs
(not for release)" comment was misleading — specs and plans are clearly
meant to be tracked. If a future subdir needs ignoring, add a more specific
rule.

### Issue 1 — `docs(enrollment): document activation state check`

The `if activation_state == "Unactivated":` comparison at
`enrollment/supervised.py:525` uses a string literal that *looks* brittle
(magic string, no constant), but matches pymobiledevice3's own
`MobileActivationService.activate()` idiom
(`pymobiledevice3/services/mobile_activation.py:105`:
`if await self.state() != "Unactivated": return`).

**Change:** Add a "Critical Behaviors & Gotchas" subsection in `AGENTS.md`
titled **Activation State String Comparison** explaining the string is not in
pymobiledevice3's public API but is the library's own convention.

### Issue 2 — `docs(skip-panes): document apple-pay → Payment mapping`

The mapping `"apple-pay": "Payment"` at `enrollment/supervised.py:52` looks
wrong (Apple Pay ≠ Payment) but is correct per Apple's
`device-management/skipkeys.yaml`, which defines `key: Payment` as the
iOS 8.1+ lockdown value for "Skips Apple Pay setup." The other 61 entries in
`SKIP_SETUP_MAPPING` were cross-checked against pymobiledevice3's
`MobileConfigService.supervise()` default skip list
(`services/mobile_config.py:201-283`) and match.

**Change:** Add a "Critical Behaviors & Gotchas" subsection in `AGENTS.md`
titled **Skip Pane Mapping Exceptions** explaining the unusual mapping and
pointing future maintainers at the reference list.

### Issue 3 — `docs(enrollment): note flows.py decision`

`enrollment/flows.py` (`SimpleSupervisedEnrollment`, `ReenrollmentFlow`,
`FlowRegistry`) is dead code — not imported by `cli.py` or any other
production module, only by its own test files
(`test_enrollment_flows.py`, `test_enrollment_integration.py`).

**Change:** Add a one-line note to the `AGENTS.md` "Architecture" section
documenting the deletion and the rationale, so future agents don't
re-introduce the abstraction.

### Issue 4 — `fix(state): rename is_mdm_managed → was_mandatorily_unpaired`

`get_device_enrollment_state()` (`enrollment/supervised.py:1001,1015,1039`)
reads the lockdown key `WasMandatorilyUnpaired` but stores it under the
local key `is_mdm_managed`. There is **no** `IsMDMEnrolled` or
`is_mdm_enrolled` in pymobiledevice3 — MDM enrollment status is derived
from `IsSupervised` + installed profile enumeration, not a single lockdown
key. `WasMandatorilyUnpaired` means the device was unpaired from mandatory
supervision (a different concept).

**Change:** Rename the local variable and dict key from `is_mdm_managed` to
`was_mandatorily_unpaired` at all 3 sites in `supervised.py`, the 1
consumer in `cli.py:1180`, and the 1 test in
`test_enrollment_flow_fixes.py:386`. No behavior change.

### Issue 5 — `chore(enrollment): delete unused flows.py`

Delete:
- `src/apple_device_cli/enrollment/flows.py` (183 lines)
- `tests/test_enrollment_flows.py` (107 lines)
- `tests/test_enrollment_integration.py` (356 lines)

Total: 646 lines of dead code. None of the 3 files is imported by any
production module.

### Issue 6 — `fix(orgs): add fcntl.flock around save_org and import_mobileconfig`

**Race condition:** Two concurrent `ios-enroll org create` invocations (or a
create racing with an import) can both pass the "does org exist?" check and
both proceed to write to the same `orgs_dir/<name>/` directory, corrupting
state.

**Approach:** `fcntl.flock(LOCK_EX)` on a per-org lock file
(`orgs_dir/.<sanitized_name>.lock`) as a context manager. Used at:
- `OrganizationManager.save_org()` (`orgs/manager.py:138-142`)
- `OrganizationManager.import_mobileconfig()` (`orgs/manager.py:310-312, 326-327`)

**Why `fcntl.flock` over alternatives:**
- `threading.Lock`: useless — CLI is one thread per process; real risk is
  cross-process.
- `filelock` library: third-party dep for ~15 lines of stdlib code.
- `os.open(O_CREAT|O_EXCL)`: works for single files but our protected region
  includes directory creation and multiple file writes.

`fcntl` is stdlib (POSIX-only; our platform is Linux per `AGENTS.md`).

**New helper:** `OrganizationManager._acquire_org_lock(name)` returns a
context manager. Lock auto-releases on exception.

### Issue 7 — `chore(enrollment): remove empty TemporaryDirectory`

`enrollment/supervised.py:541-611` wraps the entire Step 3 (cloud config
apply) in `with tempfile.TemporaryDirectory():` that **never uses the
tempdir** — no file paths inside the with block reference it. The block
builds a payload dict and calls `set_cloud_configuration` /
`get_cloud_configuration` / `_wait_for_cloud_config`, none of which touch
the filesystem.

The accompanying comment on line 534 ("Create keybag BEFORE tempdir block -
needed for MDM install after tempdir closes") exists only because the
keybag lives outside the (unused) tempdir.

**Change:** Dedent the block, drop the `with` statement, remove the
misleading comment. `tempfile` import stays (used for keybag at line 535).

### Issue 8 — `fix(enrollment): ensure keybag cleanup on exception`

`enrollment/supervised.py:535` creates a keybag file in `/tmp` containing
PEM-formatted supervision private key + certificate. Cleanup at lines
751-755 runs only in the success path. If anything in the 200+ lines
between raises (activation, config apply, WiFi install, MDM install,
verify), the file leaks.

**Change:**
- Extract cleanup into helper `_cleanup_keybag(keybag_path)` (same
  `try/except OSError` semantics).
- Wrap `do_supervised_pairing` body in `try/finally`; finally calls the
  helper.
- Existing behavior on success path is preserved (cleanup still runs, still
  swallows `OSError`).

### Issue 9 — `fix(cli): print error and exit non-zero on re-enroll failure`

`cli.py:1140-1145` (`enroll_reenroll`) catches `AppleDeviceError` from
`erase_device_for_reenrollment`, prints the error in red, but does **not**
call `raise typer.Exit(1)`. The command exits 0 even on failure, so scripts
that wrap `ios-enroll enroll re-enroll` see a successful exit code.

**Change:** Add `raise typer.Exit(1)` after the error print. Mirror the
pattern used in `enroll_status` and other commands.

## Testing

**Per-commit verification** (each commit green before the next is started):

| # | Test changes | Command |
|---|--------------|---------|
| pre | None (gitignore only) | `git status` clean, `git ls-files docs/superpowers/` shows the 11 expected paths |
| 1, 2, 3 | None (docs only) | `git diff AGENTS.md` review |
| 4 | Update 1 line: `test_enrollment_flow_fixes.py:386` key rename | `pytest tests/test_enrollment_flow_fixes.py -v` |
| 5 | None (3 files deleted) | `pytest tests/ -v` — full suite still green |
| 6 | New: `test_save_org_acquires_lock` (mock `fcntl.flock`), `test_concurrent_save_org_race` (threading + barrier) | `pytest tests/test_org_manager.py -v` |
| 7 | None (keybag lifetime unchanged) | `pytest tests/ -v` |
| 8 | New: `test_keybag_cleaned_on_exception` — patch `set_cloud_configuration` to raise, assert keybag removed | `pytest tests/test_enrollment_flow_fixes.py -v` |
| 9 | New: `test_reenroll_exits_nonzero_on_error` — patch `erase_device_for_reenrollment` to raise, assert `typer.Exit(1)` | `pytest tests/test_enrollment_flow_fixes.py -v` |

**Cross-cutting:**
- `ruff check src/ tests/` — lint clean
- `ruff format --check` — format clean
- `mypy src/` — type-check clean
- `PYTHONPATH=src python -m pytest tests/ -v` — all tests green
- Reuse existing `mock_pymobiledevice3` conftest fixture and temp-`orgs_dir`
  pattern (no `~/.config` pollution).

## Risk

- **Highest risk:** Issue 6 (locking) and Issue 8 (cleanup) — both touch
  error paths and could mask or introduce failures. Commit them
  late in the sequence and verify on real hardware if possible.
- **Lowest risk:** Issues 4, 5, 7 — pure renames / dead-code removal.
- **Docs (#1, #2, #3)** cannot fail tests by construction; the risk is
  the documentation being wrong or misleading, mitigated by cross-checking
  against pymobiledevice3 source and Apple's `skipkeys.yaml`.

## Out of Scope

- Replacing `is_mdm_managed` with a proper MDM-enrollment check (would
  require enumerating installed profiles via `get_profile_list()`) —
  follow-up issue.
- Migrating org storage from per-org directory to a database — explicitly
  rejected in earlier design discussions.
- Adding tests for `flows.py` — moot after deletion.
