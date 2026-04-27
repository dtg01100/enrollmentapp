import pytest
from unittest.mock import patch, MagicMock
from apple_device_cli.restore.erase import erase_device, update_device, restore_device
from apple_device_cli.core.exceptions import RestoreError


@patch("subprocess.run")
def test_erase_device_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    result = erase_device("test-udid")
    assert result is True


@patch("subprocess.run")
def test_erase_device_raises_on_failure(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stderr="Device not found")
    with pytest.raises(RestoreError):
        erase_device("test-udid")


@patch("subprocess.run")
def test_update_device_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    result = update_device("test-udid")
    assert result is True


@patch("subprocess.run")
def test_restore_device_success(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    result = restore_device("test-udid", "/path/to/ipsw")
    assert result is True


@patch("subprocess.run")
def test_restore_device_raises_on_failure(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stderr="Restore failed")
    with pytest.raises(RestoreError):
        restore_device("test-udid", "/path/to/ipsw")