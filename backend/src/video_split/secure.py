"""Stdlib envelope encryption for at-rest secrets.

Format: enc:v1:<nonce_hex>:<ciphertext_hex>:<tag16_hex>
Keystream: SHA256(kek + nonce + counter) XOR. Tag: HMAC-SHA256 first 16 hex.
Decrypt never raises — tamper / bad KEK / bad format returns "".
Plaintext values pass through so old files can be upgraded on load.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

PREFIX = "enc:v1:"
_NONCE_LEN = 16
_COUNTER_LEN = 4
_TAG_HEX_LEN = 16
_KEK_BYTES = 32


def _keystream(kek: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(kek + nonce + counter.to_bytes(_COUNTER_LEN, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str, kek: bytes) -> str:
    if not plaintext:
        return ""
    nonce = secrets.token_bytes(_NONCE_LEN)
    data = plaintext.encode()
    ct = bytes(a ^ b for a, b in zip(data, _keystream(kek, nonce, len(data))))
    tag = hmac.new(kek, nonce + ct, hashlib.sha256).hexdigest()[:_TAG_HEX_LEN]
    return f"{PREFIX}{nonce.hex()}:{ct.hex()}:{tag}"


def decrypt(value: str, kek: bytes) -> str:
    if not value:
        return ""
    if not value.startswith(PREFIX):
        return value
    try:
        rest = value[len(PREFIX) :]
        nonce_hex, ct_hex, tag = rest.split(":", 2)
        nonce = bytes.fromhex(nonce_hex)
        ct = bytes.fromhex(ct_hex)
        expected = hmac.new(kek, nonce + ct, hashlib.sha256).hexdigest()[:_TAG_HEX_LEN]
        if not hmac.compare_digest(expected, tag):
            return ""
        pt = bytes(a ^ b for a, b in zip(ct, _keystream(kek, nonce, len(ct))))
        return pt.decode()
    except Exception:
        return ""


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def load_or_create_kek(path: Path) -> bytes:
    """Load KEK from path; generate + chmod 0600 on first use."""
    if path.exists():
        data = path.read_bytes().strip()
        if len(data) >= _KEK_BYTES:
            return data[:_KEK_BYTES]
    kek = secrets.token_bytes(_KEK_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(kek)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return kek


def ensure_mode_0600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
