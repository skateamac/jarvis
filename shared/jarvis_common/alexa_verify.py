from __future__ import annotations

import base64
from typing import Dict
from urllib.parse import urlparse

import httpx2 as httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate

from shared.jarvis_common.config import settings

ALLOWED_CERT_HOSTS = {"s3.amazonaws.com", "s3.us-east-1.amazonaws.com"}
ALLOWED_CERT_PATH_PREFIX = "/echo.api/"


def validate_cert_url(cert_url: str) -> None:
    parsed = urlparse(cert_url)
    if parsed.scheme != "https":
        raise ValueError("Invalid Alexa certificate URL.")
    if parsed.netloc not in ALLOWED_CERT_HOSTS:
        raise ValueError("Invalid Alexa certificate URL.")
    if not parsed.path.startswith(ALLOWED_CERT_PATH_PREFIX):
        raise ValueError("Invalid Alexa certificate URL.")


def fetch_cert_pem(cert_url: str) -> bytes:
    validate_cert_url(cert_url)
    with httpx.Client(timeout=5.0) as client:
        response = client.get(cert_url)
        response.raise_for_status()
        body = response.text.strip()
    return body.encode("utf-8")


def verify_signature(cert_pem: bytes, signature_b64: str, body: bytes) -> None:
    cert = load_pem_x509_certificate(cert_pem)
    public_key = cert.public_key()
    try:
        public_key.verify(
            base64.b64decode(signature_b64),
            body,
            padding.PKCS1v15(),
            hashes.SHA1(),
        )
    except InvalidSignature as exc:
        raise ValueError("Invalid Alexa request signature.") from exc


def verify_alexa_signature_headers(headers: Dict[str, str], body: bytes) -> None:
    if settings.alexa_skip_verify:
        return
    signature = headers.get("signature") or headers.get("Signature")
    cert_url = headers.get("signaturecertchainurl") or headers.get("SignatureCertChainUrl")
    if not signature or not cert_url:
        raise ValueError("Missing Alexa signature headers.")
    if not body:
        raise ValueError("Empty Alexa request body.")
    cert_pem = fetch_cert_pem(cert_url)
    verify_signature(cert_pem, signature, body)
