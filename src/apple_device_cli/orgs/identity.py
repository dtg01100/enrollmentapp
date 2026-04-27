import base64
from pathlib import Path
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def der_to_pem(der_bytes: bytes, label: str) -> str:
    b64 = base64.b64encode(der_bytes).decode()
    lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def build_keybag_pem(cert_path: str | Path, key_path: str | Path) -> str:
    """Build concatenated cert+key PEM for pair_supervised."""
    with open(cert_path, "rb") as f:
        cert_der = f.read()
    with open(key_path, "rb") as f:
        key_der = f.read()
    return der_to_pem(cert_der, "CERTIFICATE") + der_to_pem(key_der, "PRIVATE KEY")


def generate_org_identity(org_name: str, valid_days: int = 365 * 5) -> tuple[bytes, bytes]:
    """Generate a self-signed supervising certificate and private key.

    Returns (cert_der, key_der) suitable for supervised pairing.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Cupertino"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Apple Configurator: {org_name}"),
    ])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    cert_der = cert.public_bytes(serialization.Encoding.DER)
    key_der = private_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_der, key_der


def load_cert_info(cert_der: bytes) -> dict:
    """Extract subject info from a DER-encoded certificate."""
    cert = x509.load_der_x509_certificate(cert_der)
    info = {}
    for attr in cert.subject:
        info[attr.oid] = attr.value
    return info