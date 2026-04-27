import pytest
from apple_device_cli.enrollment.skip_panes import (
    VALID_PANES,
    PRESETS,
    resolve_skip_panes,
)


def test_valid_panes_contains_expected():
    assert "appleid" in VALID_PANES
    assert "siri" in VALID_PANES
    assert "passcode" in VALID_PANES


def test_presets_contain_expected():
    assert "minimal" in PRESETS
    assert "standard" in PRESETS
    assert "all" in PRESETS


def test_resolve_skip_panes_with_preset():
    result = resolve_skip_panes("minimal", [])
    assert "restore-completed" in result
    assert "update-completed" in result


def test_resolve_skip_panes_with_extra():
    result = resolve_skip_panes("minimal", ["appleid", "siri"])
    assert "restore-completed" in result
    assert "appleid" in result
    assert "siri" in result


def test_resolve_skip_panes_invalid_raises():
    with pytest.raises(ValueError, match="Invalid panes"):
        resolve_skip_panes(None, ["invalid-pane"])


def test_resolve_skip_panes_all_preset():
    result = resolve_skip_panes("all", [])
    assert len(result) == len(VALID_PANES)
    assert "appleid" in result
    assert "siri" in result
