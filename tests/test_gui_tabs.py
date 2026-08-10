"""Tests for the TabController ABC and _NullTab test double.

Round 3 of the GUI refactor introduces ``TabController`` so each top-level
tab (Devices / Orgs / Enroll / Restore) can be implemented as its own
controller class with a uniform contract for the shell MainWindow to
drive. These tests exercise the contract in isolation so the shell
wiring has a stable surface to assert against.
"""
from __future__ import annotations

import pytest

from apple_device_cli.gui_qt.tabs import TabController, _NullTab


class TestTabControllerInterface:
    def test_null_tab_is_a_tab_controller(self):
        """_NullTab is a TabController — shell-side tests rely on this."""
        assert issubclass(_NullTab, TabController)
        assert isinstance(_NullTab(), TabController)

    def test_tab_controller_cannot_be_instantiated_directly(self):
        """TabController is abstract; instantiating it must raise TypeError."""
        with pytest.raises(TypeError):
            TabController()  # type: ignore[abstract]

    def test_null_tab_implements_all_four_methods(self):
        """Every abstract method is implemented by _NullTab."""
        tab = _NullTab()
        for name in ("widget", "refresh", "on_org_changed", "on_device_changed"):
            assert callable(getattr(tab, name)), f"_NullTab missing {name}"

    def test_null_tab_refresh_is_noop(self):
        tab = _NullTab()
        assert tab.refresh() is None

    def test_null_tab_on_org_changed_accepts_none_and_arbitrary(self):
        tab = _NullTab()
        assert tab.on_org_changed(None) is None
        assert tab.on_org_changed(object()) is None

    def test_null_tab_on_device_changed_accepts_none_and_arbitrary(self):
        tab = _NullTab()
        assert tab.on_device_changed(None) is None
        assert tab.on_device_changed(object()) is None

    def test_null_tab_widget_raises(self):
        """_NullTab.stub widget() raises so tests catch missing substitution."""
        tab = _NullTab()
        with pytest.raises(NotImplementedError):
            tab.widget()

    def test_subclass_missing_method_cannot_be_instantiated(self):
        """Forgetting to implement an abstract method blocks instantiation."""
        class HalfTab(TabController):
            def widget(self):
                return None

            def refresh(self):
                return None

        # Missing on_org_changed / on_device_changed — must not instantiate.
        with pytest.raises(TypeError):
            HalfTab()  # type: ignore[abstract]