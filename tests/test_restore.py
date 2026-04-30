import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from apple_device_cli.restore.erase import (
    enter_recovery_mode,
    erase_device,
    get_signed_firmwares,
    resolve_firmware_url,
    restore_device,
    update_device,
)
from apple_device_cli.core.exceptions import RestoreError


@pytest.fixture(autouse=True)
def mock_usbmuxd_wait():
    """Skip the usbmuxd socket wait in every restore test — no real daemon needed."""
    with patch("apple_device_cli.restore.erase._connect_usbmuxd", return_value=None):
        yield


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_erase_device_success(mock_restore_api):
    mock_restore_api.return_value = None
    result = erase_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to.ipsw")
    assert result is True
    mock_restore_api.assert_called_once()
    call_kwargs = mock_restore_api.call_args
    # behavior should be Erase
    assert call_kwargs[0][2].name == "Erase"


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_erase_device_raises_on_failure(mock_restore_api):
    mock_restore_api.side_effect = RuntimeError("device not found")
    with pytest.raises(RestoreError):
        erase_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to.ipsw")


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_erase_device_with_ecid(mock_restore_api):
    mock_restore_api.return_value = None
    result = erase_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to.ipsw")
    assert result is True
    # ecid should be converted to int and passed
    call_args = mock_restore_api.call_args[0]
    assert call_args[0] == int("0xe28e921780032", 16)


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_update_device_success(mock_restore_api):
    mock_restore_api.return_value = None
    result = update_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to.ipsw")
    assert result is True


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_update_device_with_ipsw(mock_restore_api):
    mock_restore_api.return_value = None
    result = update_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to.ipsw")
    assert result is True
    call_args = mock_restore_api.call_args[0]
    assert call_args[1] == "/path/to.ipsw"


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_update_device_with_ecid(mock_restore_api):
    mock_restore_api.return_value = None
    result = update_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to.ipsw")
    assert result is True
    call_args = mock_restore_api.call_args[0]
    assert call_args[0] == int("0xe28e921780032", 16)


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_restore_device_success(mock_restore_api):
    mock_restore_api.return_value = None
    result = restore_device("test-udid", "/path/to/ipsw", ecid="0xe28e921780032")
    assert result is True


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_restore_device_with_ecid(mock_restore_api):
    mock_restore_api.return_value = None
    result = restore_device("test-udid", "/path/to/ipsw", ecid="0xe28e921780032")
    assert result is True
    call_args = mock_restore_api.call_args[0]
    assert call_args[0] == int("0xe28e921780032", 16)


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_restore_device_raises_on_failure(mock_restore_api):
    mock_restore_api.side_effect = RuntimeError("restore failed")
    with pytest.raises(RestoreError):
        restore_device("test-udid", "/path/to/ipsw")


@patch("apple_device_cli.restore.erase.asyncio.run")
@patch("apple_device_cli.restore.erase.IRecv")
def test_enter_recovery_mode_with_ecid(mock_irecv, mock_asyncio_run):
    mock_asyncio_run.return_value = None
    mock_irecv_instance = MagicMock()
    mock_irecv_instance._device = MagicMock()
    mock_irecv.return_value = mock_irecv_instance
    result = enter_recovery_mode("test-udid", ecid="0xe28e921780032")
    assert result is True
    mock_asyncio_run.assert_called_once()
    mock_irecv.assert_called_once()


@patch("apple_device_cli.restore.erase.urlopen")
def test_get_signed_firmwares_filters_signed_builds(mock_urlopen):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"firmwares": [{"version": "18.5", "buildid": "22F76", "url": "https://example.com/18_5.ipsw", "signed": true}, {"version": "18.4", "buildid": "22E240", "url": "https://example.com/18_4.ipsw", "signed": false}]}'
    mock_urlopen.return_value = response

    firmwares = get_signed_firmwares("iPad15,7")

    assert firmwares == [
        {
            "version": "18.5",
            "buildid": "22F76",
            "url": "https://example.com/18_5.ipsw",
        }
    ]


@patch("apple_device_cli.restore.erase.get_signed_firmwares")
def test_resolve_firmware_url_matches_version_or_build(mock_get_signed_firmwares):
    mock_get_signed_firmwares.return_value = [
        {"version": "18.5", "buildid": "22F76", "url": "https://example.com/18_5.ipsw"},
    ]

    assert resolve_firmware_url("iPad15,7", "18.5") == "https://example.com/18_5.ipsw"
    assert resolve_firmware_url("iPad15,7", "22F76") == "https://example.com/18_5.ipsw"
