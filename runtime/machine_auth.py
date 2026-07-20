"""Signed-request verification for machine callers (SpawnWP Deploy plugin).

Mirrors plugins/spawnwp-deploy/src/class-spawnwp-deploy-crypto.php exactly: the canonical
string is METHOD\npath\ntimestamp\nnonce\nsha256(body) where path is the raw
URL path (e.g. /api/ingest/preflight), the signature is a base64 Ed25519
detached signature and timestamps are accepted within +-300 seconds.
"""

import base64
import hashlib
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

CLOCK_SKEW = 300
MIN_NONCE_LENGTH = 16


def canonical(method: str, path: str, timestamp: int, nonce: str, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{digest}".encode()


def generate_keypair() -> dict[str, str]:
    private = Ed25519PrivateKey.generate()
    return {
        "public": base64.b64encode(private.public_key().public_bytes_raw()).decode(),
        "private": base64.b64encode(private.private_bytes_raw()).decode(),
    }


def sign(private_b64: str, method: str, path: str, timestamp: int, nonce: str, body: bytes) -> str:
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(private.sign(canonical(method, path, timestamp, nonce, body))).decode()


def verify_detached(public_b64: str, signature_b64: str, message: bytes) -> bool:
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True))
        public.verify(base64.b64decode(signature_b64, validate=True), message)
        return True
    except (ValueError, InvalidSignature):
        return False


def verify(public_b64: str, signature_b64: str, method: str, path: str,
           timestamp: int, nonce: str, body: bytes) -> bool:
    if abs(int(time.time()) - timestamp) > CLOCK_SKEW or len(nonce) < MIN_NONCE_LENGTH:
        return False
    return verify_detached(public_b64, signature_b64, canonical(method, path, timestamp, nonce, body))
