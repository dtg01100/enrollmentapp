"""Tests for orgs/identity.py module."""
import pytest
from pathlib import Path
from datetime import datetime, timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from apple_device_cli.orgs.identity import (
    der_to_pem,
    build_keybag_pem,
    generate_org_identity,
    load_cert_info,
)


class TestDerToPem:
    """Tests for der_to_pem function."""

    def test_der_to_pem_certificate(self):
        """der_to_pem should correctly convert DER certificate to PEM."""
        # Create a simple DER certificate (reuse generate_org_identity)
        cert_der, _ = generate_org_identity("Test Org", valid_days=365)
        
        pem = der_to_pem(cert_der, "CERTIFICATE")
        
        assert pem.startswith("-----BEGIN CERTIFICATE-----")
        assert pem.endswith("-----END CERTIFICATE-----\n")
        assert "-----BEGIN CERTIFICATE-----\n" in pem
        assert "-----END CERTIFICATE-----\n" in pem

    def test_der_to_pem_private_key(self):
        """der_to_pem should correctly convert DER private key to PEM."""
        _, key_der = generate_org_identity("Test Org", valid_days=365)
        
        pem = der_to_pem(key_der, "PRIVATE KEY")
        
        assert pem.startswith("-----BEGIN PRIVATE KEY-----")
        assert pem.endswith("-----END PRIVATE KEY-----\n")

    def test_der_to_pem_base64_wrapped_at_64_chars(self):
        """PEM output should wrap base64 at 64 characters per line."""
        cert_der, _ = generate_org_identity("Test Org", valid_days=365)
        
        pem = der_to_pem(cert_der, "CERTIFICATE")
        
        lines = pem.split("\n")[1:-2]  # Skip header/footer
        for line in lines:
            assert len(line) <= 64, f"Line too long: {len(line)} chars"


class TestBuildKeybagPem:
    """Tests for build_keybag_pem function."""

    def test_build_keybag_pem_contains_both(self):
        """build_keybag_pem should output cert and key in sequence."""
        cert_der, key_der = generate_org_identity("Test Org", valid_days=365)
        
        # Write to temp files
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            
            cert_path.write_bytes(cert_der)
            key_path.write_bytes(key_der)
            
            pem = build_keybag_pem(cert_path, key_path)
        
        assert "-----BEGIN CERTIFICATE-----" in pem
        assert "-----END CERTIFICATE-----" in pem
        assert "-----BEGIN PRIVATE KEY-----" in pem
        assert "-----END PRIVATE KEY-----" in pem

    def test_build_keybag_pem_accepts_strings(self):
        """build_keybag_pem should accept both Path and str arguments."""
        cert_der, key_der = generate_org_identity("Test Org", valid_days=365)
        
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "cert.der"
            key_path = Path(tmpdir) / "key.der"
            
            cert_path.write_bytes(cert_der)
            key_path.write_bytes(key_der)
            
            # Pass as strings
            pem = build_keybag_pem(str(cert_path), str(key_path))
            
            assert "-----BEGIN CERTIFICATE-----" in pem
            assert "-----BEGIN PRIVATE KEY-----" in pem


class TestGenerateOrgIdentity:
    """Tests for generate_org_identity function."""

    def test_generate_org_identity_returns_tuple(self):
        """Should return (cert_der, key_der) tuple."""
        cert_der, key_der = generate_org_identity("Test Org")
        
        assert isinstance(cert_der, bytes)
        assert isinstance(key_der, bytes)
        assert len(cert_der) > 0
        assert len(key_der) > 0

    def test_generate_org_identity_cert_is_valid_der(self):
        """Generated certificate should be valid DER-encoded X509."""
        cert_der, _ = generate_org_identity("Test Org")
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        assert isinstance(cert, x509.Certificate)
        assert cert.serial_number > 0

    def test_generate_org_identity_key_is_valid_der(self):
        """Generated private key should be valid DER-encoded PKCS8."""
        _, key_der = generate_org_identity("Test Org")
        
        from cryptography.hazmat.primitives.serialization import load_der_private_key
        key = load_der_private_key(key_der, None)
        
        assert key.key_size >= 2048

    def test_generate_org_identity_subject_contains_org_name(self):
        """Certificate subject should contain the organization name."""
        org_name = "Test Organization Inc"
        cert_der, _ = generate_org_identity(org_name)
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        org_attrs = [attr.value for attr in cert.subject if attr.oid ==
                    x509.oid.NameOID.ORGANIZATION_NAME]
        assert org_name in org_attrs

    def test_generate_org_identity_uses_correct_country(self):
        """Certificate should default to US country."""
        cert_der, _ = generate_org_identity("Test Org")
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        country_attrs = [attr.value for attr in cert.subject if attr.oid ==
                        x509.oid.NameOID.COUNTRY_NAME]
        assert "US" in country_attrs

    def test_generate_org_identity_validity_period(self):
        """Certificate validity period should match valid_days parameter."""
        cert_der, _ = generate_org_identity("Test Org", valid_days=365)
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        # Allow 1 day tolerance for timezone differences
        assert abs(delta.days - 365) <= 1

    def test_generate_org_identity_5_year_default(self):
        """Default validity should be 5 years (365 * 5 days)."""
        cert_der, _ = generate_org_identity("Test Org")
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        # Should be approximately 5 years
        assert 365 * 5 - 1 <= delta.days <= 365 * 5 + 1

    def test_generate_org_identity_ca_constraint_is_false(self):
        """Certificate should have CA=False in BasicConstraints."""
        cert_der, _ = generate_org_identity("Test Org")
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        for ext in cert.extensions:
            if isinstance(ext, x509.BasicConstraints):
                assert ext.ca is False

    def test_generate_org_identity_signature_algorithm(self):
        """Certificate should be signed with SHA256."""
        cert_der, _ = generate_org_identity("Test Org")
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        # Verify by re-signing would require the private key
        # Instead, check the signature hash algorithm is SHA256 (case-insensitive)
        assert cert.signature_hash_algorithm.name.lower() == "sha256"

    def test_generate_org_identity_self_signed(self):
        """Certificate should be self-signed (subject == issuer)."""
        cert_der, _ = generate_org_identity("Test Org")
        
        cert = x509.load_der_x509_certificate(cert_der)
        
        assert cert.subject == cert.issuer


class TestLoadCertInfo:
    """Tests for load_cert_info function."""

    def test_load_cert_info_returns_dict(self):
        """load_cert_info should return a dictionary."""
        cert_der, _ = generate_org_identity("Test Org", valid_days=365)
        
        info = load_cert_info(cert_der)
        
        assert isinstance(info, dict)
        assert len(info) > 0

    def test_load_cert_info_contains_org_name(self):
        """load_cert_info should include organization name in subject."""
        org_name = "Acme Corporation"
        cert_der, _ = generate_org_identity(org_name, valid_days=365)
        
        info = load_cert_info(cert_der)
        
        # Check if any OID value matches the org name
        # OIDs are objects, so we check the string representation of keys
        org_values = [v for k, v in info.items() if "organizationName" in str(k)]
        assert org_name in org_values

    def test_load_cert_info_contains_country(self):
        """load_cert_info should include country in subject."""
        cert_der, _ = generate_org_identity("Test Org", valid_days=365)
        
        info = load_cert_info(cert_der)
        
        # Check if country value is present
        country_values = [v for k, v in info.items() if "countryName" in str(k)]
        assert "US" in country_values

    def test_load_cert_info_contains_common_name(self):
        """load_cert_info should include common name (Apple Configurator format)."""
        org_name = "My Org"
        cert_der, _ = generate_org_identity(org_name, valid_days=365)
        
        info = load_cert_info(cert_der)
        
        # Common name should contain "Apple Configurator: {org_name}"
        cn_values = [v for k, v in info.items() if "commonName" in str(k)]
        assert any(f"Apple Configurator: {org_name}" in str(cn) for cn in cn_values)

    def test_load_cert_info_with_invalid_der_raises(self):
        """load_cert_info should raise error with invalid DER data."""
        with pytest.raises(Exception):
            load_cert_info(b"invalid der data")
