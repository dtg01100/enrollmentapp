"""WorkerPool — single owner of background QThread instances.

Round 3 of the GUI refactor extracts the four-way duplication of
``_run_worker`` / ``_next_token`` / ``_is_current_token`` into a single
helper. The pool owns the active-worker list and exposes a ``submit``
method that wires completion + button-disable/re-enable around a
``QThread``-based worker.

Token handling stays on the caller: the pool accepts an optional
``token`` keyword and forwards it to ``on_done(result, error, token)``,
but the counter / staleness check is caller's responsibility. The
current GUI uses a single shared counter on ``MainWindow``; once tabs
are extracted, each tab can own its own counter and pass tokens to
``pool.submit``.

Patch propagation:
    ``WorkerThread`` is patched in tests by replacing
    ``apple_device_cli.gui_qt.WorkerThread`` with a synchronous fake.
    The pool does NOT instantiate workers itself — callers construct
    the worker via the package-level ``WorkerThread`` indirection so
    the existing patch path keeps working.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable


class WorkerPool:
    """Tracks in-flight workers and dispatches their completion."""

    def __init__(self) -> None:
        self._workers: list[Any] = []

    def __len__(self) -> int:
        return len(self._workers)

    def __contains__(self, worker: Any) -> bool:
        return worker in self._workers

    def submit(
        self,
        worker: Any,
        on_done: Callable,
        buttons: Iterable[Any] = (),
        token: int | None = None,
    ) -> None:
        """Start a worker, wire completion, gate UI buttons while it runs.

        Each button in ``buttons`` is disabled until the worker emits
        ``completed``. When ``token`` is given, ``on_done`` is invoked as
        ``on_done(result, error, token)`` so the caller can detect stale
        completions. Without ``token``, the callback receives
        ``on_done(result, error)``.
        """
        button_list = list(buttons)
        for btn in button_list:
            btn.setEnabled(False)

        if token is None:
            def _completed(result: Any, error: Exception | None) -> None:
                try:
                    on_done(result, error)
                finally:
                    for btn in button_list:
                        btn.setEnabled(True)

            worker.completed.connect(_completed)
        else:
            def _completed_token(result: Any, error: Exception | None) -> None:
                try:
                    on_done(result, error, token)
                finally:
                    for btn in button_list:
                        btn.setEnabled(True)

            worker.completed.connect(_completed_token)

        def _remove() -> None:
            if worker in self._workers:
                self._workers.remove(worker)

        worker.finished.connect(_remove)
        self._workers.append(worker)
        worker.start()