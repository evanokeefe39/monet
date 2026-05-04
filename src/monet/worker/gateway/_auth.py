"""HS256 JWT mint/validate using stdlib only.

No external JWT library required. Uses hmac, hashlib, base64, json from stdlib.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

__all__ = ["DEV_SIGNING_KEY", "mint_task_token", "validate_token"]

DEV_SIGNING_KEY = "monet-dev-key-not-for-production"

# Header is static for all tokens produced by this module.
_HEADER: dict[str, str] = {"alg": "HS256", "typ": "JWT"}
_HEADER_B64 = (
    base64.urlsafe_b64encode(json.dumps(_HEADER, separators=(",", ":")).encode())
    .rstrip(b"=")
    .decode()
)


def _b64url_encode(data: bytes) -> str:
    """Encode bytes as base64url with no padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """Decode base64url, adding padding as needed."""
    # Add padding so len is a multiple of 4
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(message: str, key: bytes) -> str:
    """Compute HMAC-SHA256 signature and return base64url-encoded result."""
    sig = hmac.new(key, message.encode(), hashlib.sha256).digest()
    return _b64url_encode(sig)


def mint_task_token(
    *,
    task_id: str,
    run_id: str,
    pool: str,
    scopes: list[str],
    signing_key: str,
    ttl_s: float = 3600.0,
) -> str:
    """Mint a task-scoped HS256 JWT.

    Args:
        task_id: The task this token is scoped to.
        run_id: The run this task belongs to.
        pool: Worker pool name.
        scopes: Permission scopes granted to bearer.
        signing_key: Secret used to sign the token.
        ttl_s: Token lifetime in seconds from now.

    Returns:
        Compact JWT string (header.payload.signature).
    """
    payload: dict[str, Any] = {
        "task_id": task_id,
        "run_id": run_id,
        "pool": pool,
        "scopes": scopes,
        "exp": int(time.time() + ttl_s),
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{_HEADER_B64}.{payload_b64}"
    signature = _sign(signing_input, signing_key.encode())
    return f"{signing_input}.{signature}"


def validate_token(token: str, signing_key: str) -> dict[str, Any]:
    """Validate HS256 JWT and return claims.

    Args:
        token: Compact JWT string.
        signing_key: Secret used to verify the signature.

    Returns:
        Decoded payload dict.

    Raises:
        ValueError: For malformed structure, bad signature, or expired token.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"Malformed JWT: expected 3 parts separated by '.', got {len(parts)}"
        )

    header_b64, payload_b64, signature_b64 = parts

    # Verify signature
    signing_input = f"{header_b64}.{payload_b64}"
    expected_sig = _sign(signing_input, signing_key.encode())
    if not hmac.compare_digest(expected_sig, signature_b64):
        raise ValueError("JWT signature verification failed")

    # Decode payload
    try:
        payload_json = _b64url_decode(payload_b64)
        payload: dict[str, Any] = json.loads(payload_json)
    except Exception as exc:
        raise ValueError(f"JWT payload decode failed: {exc}") from exc

    # Check expiry
    exp = payload.get("exp")
    if exp is None:
        raise ValueError("JWT missing 'exp' claim")
    if time.time() > exp:
        raise ValueError(f"JWT expired at {exp}, current time is {int(time.time())}")

    return payload
