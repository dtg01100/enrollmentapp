"""Direct unit tests for supervised enrollment helper functions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from apple_device_cli.enrollment.supervised import (
    _cloud_config_matches,
    _create_keybag_file_from_identity,
    _extract_mobileconfig_error_payload,
    _format_exception_message,
    _format_mobileconfig_error,
    _is_signed_request_rejected,
    _is_transient_mobileconfig_network_error,
    _load_cert_public_bytes_from_keybag,
    _map_skip_setup,
    _normalize_optional_path,
)


@pytest.fixture
def der_identity(tmp_path: Path) -> tuple[Path, Path, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Helper Test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    cert_path = tmp_path / "cert.der"
    key_path = tmp_path / "key.der"
    cert_path.write_bytes(cert_der)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, cert_der


class TestMapSkipSetup:
    def test_empty_list(self):
        assert _map_skip_setup([]) == []

    def test_uppercase_values_are_used_as_is(self):
        assert _map_skip_setup(["Location", "Siri"]) == ["Location", "Siri"]

    def test_lowercase_known_pane_is_mapped(self):
        assert _map_skip_setup(["location"]) == ["Location"]

    def test_unknown_lowercase_pane_is_capitalized(self):
        assert _map_skip_setup(["hello-world"]) == ["HelloWorld"]

    def test_unknown_hyphenated_pane_capitalizes_each_part(self):
        assert _map_skip_setup(["foo-bar"]) == ["FooBar"]

    def test_mixed_input_is_sorted_and_deduplicated(self):
        assert _map_skip_setup(["Siri", "location", "Siri", "foo-bar"]) == [
            "FooBar", "Location", "Siri"
        ]

    def test_apple_pay_maps_to_payment(self):
        assert _map_skip_setup(["apple-pay"]) == ["Payment"]

    def test_empty_string_is_handled(self):
        assert _map_skip_setup([""]) == [""]


class TestNormalizeOptionalPath:
    def test_none(self):
        assert _normalize_optional_path(None) is None

    def test_tilde_is_expanded(self, monkeypatch):
        monkeypatch.setattr(Path, "expanduser", lambda self: Path("expanded") / str(self)[2:])
        assert _normalize_optional_path("~/foo") == Path("expanded/foo")

    def test_path_object_is_expanded_and_remains_path(self, monkeypatch):
        monkeypatch.setattr(Path, "expanduser", lambda self: Path("expanded-path"))
        result = _normalize_optional_path(Path("~/foo"))
        assert result == Path("expanded-path")
        assert isinstance(result, Path)

    def test_single_quotes_are_removed(self):
        assert _normalize_optional_path("'foo'") == Path("foo")

    def test_double_quotes_are_removed(self):
        assert _normalize_optional_path('"foo"') == Path("foo")

    def test_quoted_whitespace_is_stripped(self):
        assert _normalize_optional_path('  "foo"  ') == Path("foo")

    def test_empty_string(self):
        assert _normalize_optional_path("") is None

    def test_whitespace_only_string(self):
        assert _normalize_optional_path("   ") is None

    def test_plain_string(self):
        assert _normalize_optional_path("foo/bar") == Path("foo/bar")

    def test_internal_whitespace_is_preserved(self):
        assert _normalize_optional_path("foo bar/baz") == Path("foo bar/baz")


class TestExtractMobileconfigErrorPayload:
    def test_exception_with_no_args(self):
        assert _extract_mobileconfig_error_payload(Exception()) is None

    def test_non_dict_args(self):
        assert _extract_mobileconfig_error_payload(Exception("boom", 1)) is None

    def test_error_chain_dict(self):
        payload = {"ErrorChain": []}
        assert _extract_mobileconfig_error_payload(Exception(payload)) is payload

    def test_status_dict(self):
        payload = {"Status": "Error"}
        assert _extract_mobileconfig_error_payload(Exception(payload)) is payload

    def test_dict_with_both_recognized_keys(self):
        payload = {"ErrorChain": [], "Status": "Error"}
        assert _extract_mobileconfig_error_payload(Exception(payload)) is payload

    def test_unrecognized_dict(self):
        assert _extract_mobileconfig_error_payload(Exception({"Other": 1})) is None

    def test_payload_embedded_in_string(self):
        payload = _extract_mobileconfig_error_payload(Exception("failed: {'ErrorChain': []}"))
        assert payload == {"ErrorChain": []}

    def test_string_without_payload(self):
        assert _extract_mobileconfig_error_payload(Exception("boom")) is None

    @pytest.mark.xfail(reason="Current helper does not recursively walk nested exception args", strict=True)
    def test_nested_args_are_walked(self):
        payload = {"Status": "Error"}
        assert _extract_mobileconfig_error_payload(Exception((payload,))) is payload


class TestFormatMobileconfigError:
    def test_no_payload_uses_exception_text(self):
        assert _format_mobileconfig_error("install", Exception("boom")) == "install: boom"

    def test_payload_without_error_chain_uses_exception_text(self):
        error = Exception({"Status": "Error"})
        assert _format_mobileconfig_error("install", error) == f"install: {error}"

    def test_chain_without_descriptions_uses_exception_text(self):
        error = Exception({"ErrorChain": [{"Code": 1}]})
        assert _format_mobileconfig_error("install", error) == f"install: {error}"

    def test_description_is_used(self):
        error = Exception({"ErrorChain": [{"LocalizedDescription": "Denied"}]})
        assert _format_mobileconfig_error("install", error) == "install: Denied"

    @pytest.mark.parametrize("description", ["Device offline", "Network error", "No internet connection"])
    def test_network_description_is_used(self, description):
        error = Exception({"ErrorChain": [{"LocalizedDescription": description}]})
        assert _format_mobileconfig_error("install", error) == f"install: {description}"

    def test_last_description_is_used_without_network_match(self):
        error = Exception({"ErrorChain": [
            {"LocalizedDescription": "First"}, {"USEnglishDescription": "Last"}
        ]})
        assert _format_mobileconfig_error("install", error) == "install: Last"

    def test_network_description_takes_priority(self):
        error = Exception({"ErrorChain": [
            {"LocalizedDescription": "First"},
            {"LocalizedDescription": "Device offline"},
            {"LocalizedDescription": "Last"},
        ]})
        assert _format_mobileconfig_error("install", error) == "install: Device offline"

    def test_duplicate_descriptions_are_deduplicated(self):
        error = Exception({"ErrorChain": [
            {"LocalizedDescription": "Last"},
            {"LocalizedDescription": "Device offline"},
            {"LocalizedDescription": "Last"},
        ]})
        assert _format_mobileconfig_error("install", error) == "install: Device offline"


class TestIsTransientMobileconfigNetworkError:
    def test_no_payload_and_empty_text(self):
        assert not _is_transient_mobileconfig_network_error(Exception())

    @pytest.mark.parametrize("text", ["DEVICE OFFLINE", "network error", "internet connection unavailable"])
    def test_matching_exception_text(self, text):
        assert _is_transient_mobileconfig_network_error(Exception(text))

    @pytest.mark.parametrize("text", ["ONLINE", "Connected"])
    def test_nonmatching_exception_text(self, text):
        assert not _is_transient_mobileconfig_network_error(Exception(text))

    def test_matching_payload_description(self):
        error = Exception({"ErrorChain": [{"LocalizedDescription": "Device offline"}]})
        assert _is_transient_mobileconfig_network_error(error)

    def test_nonmatching_payload_description(self):
        error = Exception({"ErrorChain": [{"LocalizedDescription": "Denied"}]})
        assert not _is_transient_mobileconfig_network_error(error)

    def test_plain_value_error_is_not_transient(self):
        assert not _is_transient_mobileconfig_network_error(ValueError("boom"))


class TestFormatExceptionMessage:
    def test_nonempty_exception_text(self):
        assert _format_exception_message("failed", ValueError("boom")) == "failed: boom"

    def test_empty_exception_text_uses_class_name(self):
        assert _format_exception_message("failed", ValueError()) == "failed: ValueError"


class TestCloudConfigMatches:
    def test_both_empty(self):
        assert _cloud_config_matches({}, {})

    def test_identical_simple_dicts(self):
        config = {"IsSupervised": True, "OrganizationName": "Acme"}
        assert _cloud_config_matches(config, config.copy())

    @pytest.mark.parametrize("key", ["IsSupervised", "MDMServerURL", "OrganizationName"])
    def test_different_comparable_value(self, key):
        assert not _cloud_config_matches({key: "existing"}, {key: "desired"})

    def test_missing_boolean_defaults_false_on_both_sides(self):
        assert _cloud_config_matches({"OrganizationName": "Acme"}, {"OrganizationName": "Acme"})

    def test_explicit_false_equals_missing_boolean(self):
        assert _cloud_config_matches({"IsMandatory": False}, {})

    def test_skip_setup_order_is_ignored(self):
        assert _cloud_config_matches({"SkipSetup": ["Siri", "Location"]}, {"SkipSetup": ["Location", "Siri"]})

    def test_different_skip_setup_items_do_not_match(self):
        assert not _cloud_config_matches({"SkipSetup": ["Siri"]}, {"SkipSetup": ["Location"]})

    def test_missing_post_setup_profile_defaults_false(self):
        assert _cloud_config_matches({"PostSetupProfileWasInstalled": False}, {})

    def test_default_false_and_nondefault_key_behavior_differs(self):
        assert _cloud_config_matches({"CloudConfigurationUIComplete": False}, {})
        assert not _cloud_config_matches({"ConfigurationSource": False}, {})


class TestCreateKeybagFileFromIdentity:
    def test_writes_valid_pem_with_key_and_certificate(self, tmp_path, der_identity):
        cert_path, key_path, _ = der_identity
        output = tmp_path / "identity.pem"
        _create_keybag_file_from_identity(output, cert_path, key_path)
        data = output.read_bytes()
        assert data.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert b"-----BEGIN CERTIFICATE-----" in data
        assert serialization.load_pem_private_key(data, password=None)
        assert x509.load_pem_x509_certificate(data[data.index(b"-----BEGIN CERTIFICATE-----"):])

    def test_creates_parent_directories(self, tmp_path, der_identity):
        cert_path, key_path, _ = der_identity
        output = tmp_path / "nested" / "identity.pem"
        _create_keybag_file_from_identity(output, cert_path, key_path)
        assert output.exists()

    def test_missing_certificate_raises(self, tmp_path, der_identity):
        _, key_path, _ = der_identity
        with pytest.raises(FileNotFoundError):
            _create_keybag_file_from_identity(tmp_path / "out.pem", tmp_path / "missing.der", key_path)

    def test_missing_key_raises(self, tmp_path, der_identity):
        cert_path, _, _ = der_identity
        with pytest.raises(FileNotFoundError):
            _create_keybag_file_from_identity(tmp_path / "out.pem", cert_path, tmp_path / "missing.der")


class TestLoadCertPublicBytesFromKeybag:
    def test_extracts_certificate_der(self, tmp_path, der_identity):
        cert_path, key_path, cert_der = der_identity
        keybag = tmp_path / "identity.pem"
        _create_keybag_file_from_identity(keybag, cert_path, key_path)
        assert _load_cert_public_bytes_from_keybag(keybag) == cert_der

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_cert_public_bytes_from_keybag(tmp_path / "missing.pem")

    def test_empty_file_raises(self, tmp_path):
        keybag = tmp_path / "empty.pem"
        keybag.write_bytes(b"")
        with pytest.raises(ValueError, match="Certificate not found"):
            _load_cert_public_bytes_from_keybag(keybag)


class TestIsSignedRequestRejected:
    @pytest.mark.parametrize(
        "text",
        [
            "invalid response {'Status': 'SignedRequestRejected'}",
            "SignedRequestRejected",
        ],
    )
    def test_matching_exception_text(self, text):
        assert _is_signed_request_rejected(Exception(text))

    def test_matching_dict_arg(self):
        assert _is_signed_request_rejected(Exception({"Status": "SignedRequestRejected"}))

    def test_nonmatching_text(self):
        assert not _is_signed_request_rejected(Exception("boom"))

    def test_other_status_dict_is_not_rejected(self):
        assert not _is_signed_request_rejected(Exception({"Status": "Error"}))


class TestInstallProfileSilentWithRetry:
    def _run(self, results, **kwargs):
        from apple_device_cli.enrollment import supervised

        shared_results = list(results)
        call_count = {"n": 0}

        class FakeService:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args, **kwargs):
                return False

            async def install_profile_silent(self, keybag_path, payload):
                call_count["n"] += 1
                result = shared_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

        raised = None
        with patch(
            "apple_device_cli.enrollment.supervised._get_mobile_config_service",
            return_value=lambda lockdown: FakeService(),
        ), patch(
            "apple_device_cli.enrollment.supervised.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            try:
                asyncio.run(
                    supervised._install_profile_silent_with_retry(
                        lockdown=None,
                        keybag_path=Path("/tmp/keybag"),
                        payload=b"payload",
                        **kwargs,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raised = exc
        return call_count["n"], mock_sleep, raised

    def test_retries_signed_request_rejected_then_succeeds(self):
        rejected = Exception("invalid response {'Status': 'SignedRequestRejected'}")
        calls, mock_sleep, raised = self._run([rejected, None])
        assert calls == 2
        assert raised is None
        mock_sleep.assert_awaited_once()

    def test_does_not_retry_other_errors(self):
        calls, mock_sleep, raised = self._run([ValueError("boom")])
        assert calls == 1
        assert isinstance(raised, ValueError)
        mock_sleep.assert_not_awaited()

    def test_exhausts_retries_and_raises_last_error(self):
        rejected = Exception("invalid response {'Status': 'SignedRequestRejected'}")
        calls, mock_sleep, raised = self._run([rejected, rejected, rejected], max_attempts=3)
        assert calls == 3
        assert raised is not None
        assert "SignedRequestRejected" in str(raised)
        assert mock_sleep.await_count == 2
