"""Self-signed certificate generation for the MCP server's own HTTPS (NOT the Hue
bridge connection — that TLS is handled by the python-hue-v2 library).

Generated on first boot into the state dir if absent; served at GET /cert for pinning.
"""

from __future__ import annotations

import datetime
import ipaddress
import os
from pathlib import Path


def ensure_self_signed(cert_path: Path, key_path: Path, host: str = "hueMCP.local") -> str:
    """Generate a self-signed cert/key pair if missing. Returns the certificate PEM text."""
    if cert_path.exists() and key_path.exists():
        return cert_path.read_text()

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    alt_names = [x509.DNSName(host), x509.DNSName("localhost")]
    try:
        alt_names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    except ValueError:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    # The private key is unencrypted on disk, so it must never be world-readable: create
    # it 0600 before writing (fchmod also tightens a pre-existing key file).
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key_bytes)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(cert_pem)
    return cert_pem.decode()
