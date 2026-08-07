"""Tests for the streaming worker thread used by the Restore tab.

The existing WorkerThread in gui_qt.py is a fire-and-forget callable.
The restore engine produces a stream of progress lines + a final
result. StreamingWorkerThread wraps Popen, reads stdout line by line,
emits each line via a signal, and emits the final result on exit.
"""
from __future__ import annotations

import sys

import pytest

# Skip the whole module if PySide6 isn't available
pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def flush_events():
    """Spin the Qt event loop so queued signal deliveries get dispatched.

    ``QApplication.processEvents()`` is racy here — it sometimes returns
    before queued ``Signal`` emissions posted from a finished QThread are
    delivered. A short real event-loop spin deterministically flushes them.
    """
    loop = QEventLoop()
    QTimer.singleShot(50, loop.quit)
    loop.exec()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
    flush_events()


class TestStreamingWorkerThread:
    def test_emits_progress_lines_and_final_result(self, qapp, tmp_path):
        from apple_device_cli.gui_qt import StreamingWorkerThread

        # Use a real subprocess that prints 3 lines and exits
        script = tmp_path / "echo.py"
        script.write_text(
            "import sys\n"
            "for line in ('line1', 'line2', 'line3'):\n"
            "    print(line, flush=True)\n"
            "sys.exit(0)\n"
        )

        progress_lines: list[str] = []
        results: list[tuple] = []

        worker = StreamingWorkerThread(
            cmd=[sys.executable, str(script)],
            on_progress=progress_lines.append,
            on_finished=lambda result, error: results.append((result, error)),
        )
        worker.start()
        worker.wait(10_000)  # 10s timeout for the test itself
        flush_events()

        assert progress_lines == ["line1", "line2", "line3"]
        assert len(results) == 1
        result, error = results[0]
        assert error is None
        assert result["returncode"] == 0

    def test_emits_nonzero_exit(self, qapp, tmp_path):
        from apple_device_cli.gui_qt import StreamingWorkerThread

        script = tmp_path / "fail.py"
        script.write_text("import sys\nprint('boom'); sys.exit(2)\n")

        results: list[tuple] = []
        worker = StreamingWorkerThread(
            cmd=[sys.executable, str(script)],
            on_progress=lambda line: None,
            on_finished=lambda result, error: results.append((result, error)),
        )
        worker.start()
        worker.wait(10_000)
        flush_events()

        assert len(results) == 1
        result, error = results[0]
        assert error is None  # not an exception
        assert result["returncode"] == 2

    def test_exception_in_run_still_emits_result(self, qapp, tmp_path):
        """A missing executable surfaces as (None, exc) rather than hanging."""
        from apple_device_cli.gui_qt import StreamingWorkerThread

        results: list[tuple] = []
        worker = StreamingWorkerThread(
            cmd=["/nonexistent/definitely-not-a-binary"],
            on_progress=lambda line: None,
            on_finished=lambda result, error: results.append((result, error)),
        )
        worker.start()
        worker.wait(10_000)
        flush_events()

        assert len(results) == 1
        result, error = results[0]
        assert result is None
        assert isinstance(error, Exception)
