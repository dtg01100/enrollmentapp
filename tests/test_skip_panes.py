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


def test_resolve_skip_panes_empty_extra():
    """Empty extra list should work with any preset."""
    result = resolve_skip_panes("standard", [])
    assert "appleid" in result
    assert "restore-completed" in result


def test_resolve_skip_panes_none_extra():
    """None extra list should work (treated as empty)."""
    result = resolve_skip_panes("standard", None)
    assert "appleid" in result


def test_resolve_skip_panes_duplicates_in_extra():
    """Duplicate panes in extra list should not cause duplicates in result."""
    result = resolve_skip_panes("minimal", ["appleid", "appleid", "appleid"])
    # Count occurrences of appleid
    count = result.count("appleid")
    assert count == 1


def test_resolve_skip_panes_invalid_case_sensitive():
    """Invalid panes are case-sensitive (exact match required)."""
    with pytest.raises(ValueError, match="Invalid panes"):
        resolve_skip_panes(None, ["AppleID"])  # Wrong case


def test_resolve_skip_panes_invalid_partial_match():
    """Invalid panes should not partial-match valid ones."""
    with pytest.raises(ValueError, match="Invalid panes"):
        resolve_skip_panes(None, ["apple"])


def test_resolve_skip_panes_preset_with_extra_override():
    """Preset panes and extra panes should be combined."""
    result = resolve_skip_panes("minimal", ["siri", "location"])
    
    # Should contain minimal preset panes
    assert "restore-completed" in result
    assert "update-completed" in result
    # Should contain extra panes
    assert "siri" in result
    assert "location" in result


def test_resolve_skip_panes_unknown_preset_ignored():
    """Unknown preset should be ignored (not raise error)."""
    result = resolve_skip_panes("nonexistent_preset", ["appleid"])
    # Should just return extra panes
    assert "appleid" in result
    assert len(result) == 1


def test_resolve_skip_panes_result_is_sorted():
    """Result should always be sorted alphabetically."""
    result = resolve_skip_panes(None, ["watch-migration", "appleid", "location"])
    
    assert result == sorted(result)
    assert result == ["appleid", "location", "watch-migration"]


def test_resolve_skip_panes_no_preset_no_extra():
    """No preset, no extra should return empty list."""
    result = resolve_skip_panes(None, [])
    assert result == []


def test_resolve_skip_panes_multiple_invalid():
    """Multiple invalid panes should list all in error message."""
    with pytest.raises(ValueError, match="Invalid panes"):
        resolve_skip_panes(None, ["foo", "bar", "baz"])


def test_presets_minimal_contains_only_expected():
    """minimal preset should contain exactly restore-completed and update-completed."""
    assert PRESETS["minimal"] == ["restore-completed", "update-completed"]


def test_presets_standard_contains_expected_items():
    """standard preset should contain expected skip panes."""
    expected = {"restore-completed", "update-completed", "appleid", "passcode", "siri", "location", "home-button"}
    assert set(PRESETS["standard"]) == expected


def test_presets_all_contains_all_valid():
    """all preset should contain all valid panes."""
    assert set(PRESETS["all"]) == VALID_PANES


def test_valid_panes_count():
    """VALID_PANES should contain expected number of panes."""
    # This is a regression test - if panes are added/removed, this test will catch it
    assert len(VALID_PANES) == 43  # Current count
