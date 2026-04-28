"""HTTP API key generation, hashing, and attribution."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sysbench_devices.errors import AuthenticationError
from sysbench_devices.models import ApiKeyRecord, ReservationAttribution

_HASH_NAME = "pbkdf2_sha256"
_ITERATIONS = 120_000


@dataclass(frozen=True)
class CreatedApiKey:
    record: ApiKeyRecord
    secret: str


def generate_secret() -> str:
    return f"sbdev_{secrets.token_urlsafe(32)}"


def hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, _ITERATIONS)
    return "$".join(
        [
            _HASH_NAME,
            str(_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_secret(secret: str, encoded_hash: str) -> bool:
    try:
        name, iterations_text, salt_text, digest_text = encoded_hash.split("$", 3)
        if name != _HASH_NAME:
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_api_key(key_id: str, label: str) -> CreatedApiKey:
    secret = generate_secret()
    return CreatedApiKey(
        record=ApiKeyRecord(id=key_id, label=label, key_hash=hash_secret(secret)),
        secret=secret,
    )


def resolve_attribution(secret: str, records: tuple[ApiKeyRecord, ...]) -> ReservationAttribution:
    for record in records:
        if not record.revoked and verify_secret(secret, record.key_hash):
            return ReservationAttribution(kind="api_key", id=record.id, label=record.label)
    raise AuthenticationError("unknown or revoked API key")
