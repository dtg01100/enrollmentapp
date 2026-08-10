"""Tests for the _Gating helper.

Round 3 step 9: shell-side helper that tracks org + device presence
and tells tabs whether enrollment actions are enableable.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from apple_device_cli.gui_qt.gating import GatingState, _Gating


class TestGatingInitialState:
    def test_starts_with_no_org_no_device(self):
        g = _Gating()
        assert g.state() == GatingState(has_org=False, has_device=False)
        assert g.can_enroll() is False

    def test_evaluate_enroll_returns_false_when_nothing_set(self):
        g = _Gating()
        assert g.evaluate_for("enroll") is False


class TestGatingOrgPresence:
    def test_set_org_with_value(self):
        g = _Gating()
        g.set_org(MagicMock())
        assert g.state().has_org is True
        assert g.can_enroll() is False  # device still missing

    def test_set_org_with_none(self):
        g = _Gating()
        g.set_org(MagicMock())
        g.set_org(None)
        assert g.state().has_org is False

    def test_set_org_called_with_truthy_object_marks_present(self):
        g = _Gating()
        g.set_org(object())
        assert g.state().has_org is True


class TestGatingDevicePresence:
    def test_set_device_with_value(self):
        g = _Gating()
        g.set_device(MagicMock())
        assert g.state().has_device is True
        assert g.can_enroll() is False  # org still missing


class TestGatingCanEnroll:
    def test_both_set_enables_enrollment(self):
        g = _Gating()
        g.set_org(MagicMock())
        g.set_device(MagicMock())
        assert g.can_enroll() is True
        assert g.evaluate_for("enroll") is True

    def test_clearing_one_disables(self):
        g = _Gating()
        g.set_org(MagicMock())
        g.set_device(MagicMock())
        g.set_org(None)
        assert g.can_enroll() is False


class TestGatingEvaluate:
    def test_unknown_action_returns_false(self):
        g = _Gating()
        g.set_org(MagicMock())
        g.set_device(MagicMock())
        assert g.evaluate_for("restore") is False
        assert g.evaluate_for("foo") is False

    def test_clear_resets_state(self):
        g = _Gating()
        g.set_org(MagicMock())
        g.set_device(MagicMock())
        g.clear()
        assert g.state() == GatingState(has_org=False, has_device=False)