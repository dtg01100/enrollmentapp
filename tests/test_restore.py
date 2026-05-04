import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from apple_device_cli.restore.erase import (
    _check_disk_space,
    _download_ipsw,
    _ensure_ipsw_local,
    enter_recovery_mode,
    erase_device,
    get_signed_firmwares,
    InsufficientSpaceError,
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
    result = restore_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to/ipsw")
    assert result is True


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_restore_device_with_ecid(mock_restore_api):
    mock_restore_api.return_value = None
    result = restore_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to/ipsw")
    assert result is True
    call_args = mock_restore_api.call_args[0]
    assert call_args[0] == int("0xe28e921780032", 16)


@patch("apple_device_cli.restore.erase._restore_with_api")
def test_restore_device_raises_on_failure(mock_restore_api):
    mock_restore_api.side_effect = RuntimeError("restore failed")
    with pytest.raises(RestoreError):
        restore_device("test-udid", ecid="0xe28e921780032", ipsw="/path/to/ipsw")


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


class TestCheckDiskSpace:
    @patch("os.statvfs")
    def test_has_space(self, mock_statvfs):
        mock_statvfs.return_value = MagicMock(f_bavail=1000000, f_frsize=4096)
        has_space, available = _check_disk_space(Path("/some/path"), 1000000)
        assert has_space is True
        assert available == 1000000 * 4096

    @patch("os.statvfs")
    def test_insufficient_space(self, mock_statvfs):
        mock_statvfs.return_value = MagicMock(f_bavail=100, f_frsize=4096)
        has_space, available = _check_disk_space(Path("/some/path"), 1000000)
        assert has_space is False
        assert available == 100 * 4096

    @patch("os.statvfs")
    def test_os_error_returns_true(self, mock_statvfs):
        mock_statvfs.side_effect = OSError("permission denied")
        has_space, available = _check_disk_space(Path("/some/path"), 1000000)
        assert has_space is True
        assert available == 0


class TestInsufficientSpaceError:
    def test_error_attributes(self):
        err = InsufficientSpaceError(needed_mb=5000, available_mb=1000, target_dir=Path("/tmp"))
        assert err.needed_mb == 5000
        assert err.available_mb == 1000
        assert err.target_dir == Path("/tmp")
        assert "5000" in str(err)
        assert "1000" in str(err)
        assert "/tmp" in str(err)


class TestDownloadIpsw:
    @patch("apple_device_cli.restore.erase.urlopen")
    @patch("apple_device_cli.restore.erase._check_disk_space")
    def test_download_success(self, mock_check_space, mock_urlopen, tmp_path):
        mock_check_space.return_value = (True, 0)
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get.return_value = "5000000"
        response.read.side_effect = [b"chunk1", b"chunk2", b""]
        mock_urlopen.return_value = response

        result = _download_ipsw("https://example.com/test.ipsw", timeout=60, work_dir=str(tmp_path))

        assert result.name == "test.ipsw"
        mock_urlopen.assert_called_once()

    @patch("apple_device_cli.restore.erase.urlopen")
    @patch("apple_device_cli.restore.erase._check_disk_space")
    def test_insufficient_space_raises(self, mock_check_space, mock_urlopen, tmp_path):
        mock_check_space.return_value = (False, 100 * 1024 * 1024)
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get.return_value = "5000000000"
        mock_urlopen.return_value = response

        with pytest.raises(InsufficientSpaceError) as exc_info:
            _download_ipsw("https://example.com/test.ipsw", timeout=60, work_dir=str(tmp_path))

        assert exc_info.value.needed_mb > 0
        assert exc_info.value.available_mb == 100

    @patch("apple_device_cli.restore.erase.urlopen")
    def test_download_failure_cleans_up_partial_file(self, mock_urlopen, tmp_path):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers.get.return_value = "5000000"
        response.read.side_effect = [b"partial", Exception("network error")]
        mock_urlopen.return_value = response

        with pytest.raises(RestoreError) as exc_info:
            _download_ipsw("https://example.com/test.ipsw", timeout=60, work_dir=str(tmp_path))

        assert "Failed to download" in str(exc_info.value)
        assert not (tmp_path / "test.ipsw").exists()


class TestEnsureIpswLocal:
    def test_local_file(self, tmp_path):
        ipsw_file = tmp_path / "test.ipsw"
        ipsw_file.write_bytes(b"fake ipsw")
        result = _ensure_ipsw_local(str(ipsw_file))
        assert result == ipsw_file

    def test_missing_local_file_raises(self):
        with pytest.raises(RestoreError) as exc_info:
            _ensure_ipsw_local("/nonexistent/path.ipsw")
        assert "not found" in str(exc_info.value)

    @patch("apple_device_cli.restore.erase._download_ipsw")
    def test_url_downloads(self, mock_download, tmp_path):
        mock_download.return_value = tmp_path / "downloaded.ipsw"
        _ensure_ipsw_local("https://example.com/test.ipsw", work_dir=str(tmp_path))
        mock_download.assert_called_once_with("https://example.com/test.ipsw", work_dir=str(tmp_path), progress_callback=None)
