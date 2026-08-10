# Round 3 — `EnrollmentApp` refactor

## Why

`src/apple_device_cli/gui_qt.py` is 2949 LOC. `EnrollmentApp` alone is **2662 LOC** (lines 287–2949), **95 methods**, **18 slots** on one class. Rounds 1 + 2 added features by piling onto the god class. Time to split before the next feature lands.

## Decisions (resolving the open questions)

1. **TabController ABC** — small abstract base class in `gui_qt/tabs.py` defining `widget()`, `refresh()`, `on_org_changed(org)`, `on_device_changed(device)`. Lets tests instantiate each controller in isolation; no full window setup needed for unit tests.
2. **Shared WorkerPool** — `gui_qt/worker.py` exposes a `WorkerPool` that owns the worker thread + token increment. Each tab calls `pool.submit(fn, on_done, token)` with its own token stream. Keeps Round 1's per-tab token pattern; removes four-way duplication of `_run_worker`.
3. **Gating on the shell** — `gui_qt/gating.py` defines a `_Gating` helper that lives on `MainWindow` (sees org + device presence). `EnrollTab` consults it for button enable/disable; `DevicesTab` context menu consults it to hide "Make Supervised" when no org is selected (closes Round 2's open question).

## File layout

The top-level `src/apple_device_cli/gui_qt.py` is deleted and replaced by a package `gui_qt/`:

```
src/apple_device_cli/gui_qt/
├── __init__.py      # back-compat: `from .app import MainWindow as EnrollmentApp`
├── app.py           # MainWindow — owns tabs, status bar, QSettings, gating, worker pool
├── tabs.py          # TabController ABC + DevicesTab / OrgsTab / EnrollTab / RestoreTab
├── worker.py        # WorkerPool + WorkerToken
└── gating.py        # _Gating
```

The public import `from apple_device_cli.gui_qt import EnrollmentApp` keeps working through `__init__.py`.

## Task list (TDD-ordered)

1. **Package skeleton** — Create `gui_qt/` directory + `__init__.py` that re-exports from the existing top-level `gui_qt.py` (rename `gui_qt.py` → `gui_qt/app.py`, leave `__init__.py` as `from .app import *`). Delete the now-empty top-level `gui_qt.py`. All tests still pass.
2. **WorkerPool** — Extract `_run_worker`, `_next_token`, `_is_current_token` into `WorkerPool` (`gui_qt/worker.py`). `MainWindow` (still currently `EnrollmentApp`) delegates to it. All existing tests pass unchanged.
3. **TabController ABC** — Add `gui_qt/tabs.py` with the `TabController` ABC + a `_NullTab` test double. No consumers yet. One test asserts interface conformance.
4. **DevicesTab** — Move `_create_devices_tab` plus `_refresh_devices*`, `_show_device_info*`, `_activate_device*`, `_pair_device*`, `_show_devices_context_menu`, `_build_devices_context_menu`, `_make_supervised_from_context`, `_update_enroll_udids` into `DevicesTab`. `MainWindow` instantiates it. New `tests/test_gui_devices_tab.py` instantiates `DevicesTab` directly with a fake manager.
5. **OrgsTab** — Same pattern: `_create_orgs_tab`, `_refresh_orgs*`, `_update_enroll_orgs`, `_on_enroll_org_changed`, `_update_org_details`, `_create_org_dialog`, `_save_org_dialog`, `_edit_org`, `_delete_org`, `_import_org`, `_export_org`, `_attach_wifi`, `_set_cert`, `_set_key`, `_set_mdm_url`, `_set_checkin_url`, `_set_mdm_topic`.
6. **EnrollTab** — Move `_create_enroll_tab`, `_cert_expiry*`, `_format_cert_expiry_badge`, `_update_enroll_cert_banner`, `_guided_enroll`, `_confirm_restore`, `_run_enroll`, WiFi + MDM profile orchestration.
7. **RestoreTab** — Move `_create_restore_tab`, `_log_to_restore`, `_append_restore_log`, `_clear_cache`, `_run_restore`.
8. **MainWindow shell** — Rename `EnrollmentApp` → `MainWindow`. Strip everything that moved into tabs. `EnrollmentApp = MainWindow` alias for back-compat. Shell owns status bar, `QSettings`, geometry persistence.
9. **`_Gating` helper** — Add `gui_qt/gating.py`. `MainWindow` instantiates it; signals `org_changed` + `device_changed` into it. `EnrollTab` consults it for button enable/disable; `DevicesTab` context menu consults it for "Make Supervised" visibility (closes Round 2 open question).
10. **Auto-refresh `QTimer`** — Add `QTimer` on `MainWindow` (interval from `QSettings`, default 5 s). On tick, call `refresh()` on each tab. Was blocked on the refactor.
11. **Per-tab `OrganizationManager`** — Each tab controller owns its own `OrganizationManager` instance. Removes the implicit coupling where one shared manager served all tabs.
12. **Test split** — Break `tests/test_gui_qt.py` into per-tab test files (`tests/test_gui_devices_tab.py`, `test_gui_orgs_tab.py`, `test_gui_enroll_tab.py`, `test_gui_restore_tab.py`) plus `tests/test_main_window.py`. Each controller gets direct unit tests without instantiating a full window.

## Out of scope

- Dark-theme QSS overhaul — cosmetic, low ROI.
- Background device-mode polling — refresh-on-action is fine.
- New user-facing features — pure structural refactor.

## Done when

- `gui_qt.py` no longer exists at the top level; `gui_qt/` package has 5 files, all under 600 LOC.
- No class in the repo exceeds ~500 LOC.
- `from apple_device_cli.gui_qt import EnrollmentApp` still works.
- All 17 Round 1 + 2 features still work — no behavior changes.
- `DevicesTab` context menu hides "Make Supervised" when no org is selected.
- Auto-refresh `QTimer` is wired and active.
- `OrganizationManager` is owned per-tab.
- `pytest tests/` passes.