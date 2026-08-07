"""Tests for the iOS-26 pair-then-retry connect wrapper.

Covers the four branches of ``_pair_then_retry_connect``:

1. First connect succeeds → returns the lockdown object, never calls pairing.
2. First connect fails (ConnectionError) → pairing runs, device reappears
   via usbmux, second connect succeeds → returns the new lockdown object.
3. First connect fails AND second connect also fails → original error is
   re-raised (NOT the second one, which would mask the real cause).
4. If the device never reappears in usbmux, don't try again → re-raise the
   original error.
5. A non-ConnectionError on the first attempt is NOT a pair/trust issue.
   The wrapper must not eat the exception by trying to pair — that would
   mask the real cause.

Async tests are run with ``asyncio.run`` (sync methods) to match the
project's existing test style — pytest-asyncio is not in the test deps.

The wrapper matches against ``ConnectionError`` (the parent of all the
pymobiledevice3 connection exceptions), so we test with bare
``ConnectionError`` to stay decoupled from the specific subclass names
pymobiledevice3 may rename between versions.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from apple_device_cli.enrollment import supervised


def _run(coro):
    """Run a coroutine in a sync test, matching the project's existing style."""
    return asyncio.run(coro)


class TestPairThenRetryConnect:
    """Direct tests of the new pair-on-failure wrapper."""

    def test_first_connect_succeeds_skips_pairing(self):
        """Happy path: no pair call, no usbmux poll, return the original lockdown."""
        first_lockdown = MagicMock(name="first_lockdown")

        connect = AsyncMock(return_value=first_lockdown)
        pair = MagicMock()
        wait = MagicMock()

        result = _run(
            supervised._pair_then_retry_connect(
                udid="UDID-A",
                connect=connect,
                ensure_pairing=pair,
                wait_for_udid=wait,
            )
        )

        assert result is first_lockdown
        connect.assert_awaited_once_with(serial="UDID-A")
        pair.assert_not_called()
        wait.assert_not_called()

    def test_first_fails_pair_then_retry_succeeds(self):
        """ConnectionError → ensure_pairing → wait → second connect succeeds."""
        new_lockdown = MagicMock(name="new_lockdown")
        connect = AsyncMock(side_effect=[ConnectionError(), new_lockdown])
        pair = MagicMock()
        wait = MagicMock(return_value=True)

        result = _run(
            supervised._pair_then_retry_connect(
                udid="UDID-B",
                connect=connect,
                ensure_pairing=pair,
                wait_for_udid=wait,
            )
        )

        assert result is new_lockdown
        assert connect.await_count == 2
        assert connect.await_args_list[0].kwargs == {"serial": "UDID-B"}
        assert connect.await_args_list[1].kwargs == {"serial": "UDID-B"}
        pair.assert_called_once_with("UDID-B")
        wait.assert_called_once_with("UDID-B")

    def test_first_fails_retry_also_fails_reraises_original(self):
        """Both connects fail → re-raise the ORIGINAL error, not the second one.

        The second failure could be misleading (e.g. "still not paired") —
        surfacing the original error preserves the diagnostic chain.
        """
        original = ConnectionError()
        second = ConnectionError("retry also failed")
        connect = AsyncMock(side_effect=[original, second])
        pair = MagicMock()
        wait = MagicMock(return_value=True)

        with pytest.raises(ConnectionError) as excinfo:
            _run(
                supervised._pair_then_retry_connect(
                    udid="UDID-C",
                    connect=connect,
                    ensure_pairing=pair,
                    wait_for_udid=wait,
                )
            )

        assert excinfo.value is original
        pair.assert_called_once_with("UDID-C")

    def test_first_fails_wait_returns_false_skips_retry(self):
        """If the device never reappears in usbmux, don't try again.

        ``wait_for_udid_in_usbmux`` returning False means the device didn't
        show back up after the pair prompt. A second connect attempt would
        just fail identically. Bail with the original error.
        """
        original = ConnectionError()
        connect = AsyncMock(side_effect=[original])
        pair = MagicMock()
        wait = MagicMock(return_value=False)

        with pytest.raises(ConnectionError) as excinfo:
            _run(
                supervised._pair_then_retry_connect(
                    udid="UDID-D",
                    connect=connect,
                    ensure_pairing=pair,
                    wait_for_udid=wait,
                )
            )

        assert excinfo.value is original
        assert connect.await_count == 1
        pair.assert_called_once_with("UDID-D")
        wait.assert_called_once_with("UDID-D")

    def test_non_connection_error_passes_through_without_pairing(self):
        """A non-ConnectionError on the first attempt is NOT a pair/trust issue.

        Examples: invalid UDID, programmer error, library bug. The wrapper
        must not eat the exception by trying to pair — that would mask the
        real cause.
        """
        boom = ValueError("not a pairing problem")
        connect = AsyncMock(side_effect=[boom])
        pair = MagicMock()
        wait = MagicMock()

        with pytest.raises(ValueError) as excinfo:
            _run(
                supervised._pair_then_retry_connect(
                    udid="UDID-E",
                    connect=connect,
                    ensure_pairing=pair,
                    wait_for_udid=wait,
                )
            )

        assert excinfo.value is boom
        pair.assert_not_called()
        wait.assert_not_called()
        assert connect.await_count == 1

    def test_bare_connection_error_triggers_pair(self):
        """A plain ``ConnectionError`` is treated as a pairing failure.

        ``ConnectionError`` is the parent of all the pymobiledevice3
        connection exceptions (``ConnectionFailedToUsbmuxdError``,
        ``NoDeviceConnectedError``, ``DeviceNotFoundError``). Matching
        against ``ConnectionError`` — not a specific subclass — keeps the
        wrapper resilient to library renames between pymobiledevice3
        versions (project pins ``>=9.12.0``; iOS 26 support ships in 10.x).
        """
        first_err = ConnectionError("plain connection")
        new_lockdown = MagicMock(name="new_lockdown")
        connect = AsyncMock(side_effect=[first_err, new_lockdown])
        pair = MagicMock()
        wait = MagicMock(return_value=True)

        result = _run(
            supervised._pair_then_retry_connect(
                udid="UDID-F",
                connect=connect,
                ensure_pairing=pair,
                wait_for_udid=wait,
            )
        )

        assert result is new_lockdown
        pair.assert_called_once_with("UDID-F")
