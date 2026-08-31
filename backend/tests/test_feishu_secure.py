from __future__ import annotations

import os
from pathlib import Path

from video_split.secure import decrypt, encrypt, is_encrypted, load_or_create_kek


def test_encrypt_roundtrip():
    kek = b"k" * 32
    assert decrypt(encrypt("hello-secret", kek), kek) == "hello-secret"


def test_encrypt_nonce_makes_ciphertext_unique():
    kek = b"k" * 32
    a = encrypt("same-plain", kek)
    b = encrypt("same-plain", kek)
    assert a != b
    assert decrypt(a, kek) == "same-plain"
    assert decrypt(b, kek) == "same-plain"


def test_decrypt_tamper_returns_empty():
    kek = b"k" * 32
    token = encrypt("secret", kek)
    # flip one hex nibble in the ciphertext segment
    prefix, nonce, ct, tag = token.split(":", 3)
    flipped = ("0" if ct[0] != "0" else "1") + ct[1:]
    assert decrypt(f"{prefix}:{nonce}:{flipped}:{tag}", kek) == ""


def test_plaintext_passthrough():
    assert decrypt("not-encrypted", b"k" * 32) == "not-encrypted"
    assert not is_encrypted("not-encrypted")
    assert is_encrypted(encrypt("x", b"k" * 32))


def test_kek_created_0600(tmp_path: Path):
    path = tmp_path / "secret.key"
    kek = load_or_create_kek(path)
    assert path.exists()
    assert len(kek) == 32
    assert (os.stat(path).st_mode & 0o777) == 0o600
    assert load_or_create_kek(path) == kek
