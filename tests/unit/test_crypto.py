import os

import pytest

from core.security.crypto import ChunkCipher, get_cipher


def test_roundtrip_and_versioned_prefix():
    cipher = ChunkCipher(os.urandom(32).hex())
    ciphertext = cipher.encrypt("Article 6 — Lawfulness of processing")
    assert ciphertext.startswith("enc1:")
    assert "Lawfulness" not in ciphertext
    assert cipher.decrypt(ciphertext) == "Article 6 — Lawfulness of processing"


def test_legacy_plaintext_passes_through():
    # rows written before the key was enabled must still read
    cipher = ChunkCipher(os.urandom(32).hex())
    assert cipher.decrypt("plain unencrypted text") == "plain unencrypted text"


def test_nonce_makes_ciphertext_nondeterministic():
    cipher = ChunkCipher(os.urandom(32).hex())
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_wrong_key_cannot_decrypt():
    a = ChunkCipher(os.urandom(32).hex())
    b = ChunkCipher(os.urandom(32).hex())
    with pytest.raises(Exception):
        b.decrypt(a.encrypt("secret"))


def test_bad_key_size_rejected():
    with pytest.raises(ValueError, match="64 hex"):
        ChunkCipher("abcd")


def test_get_cipher_none_disables():
    assert get_cipher(None) is None
    assert get_cipher("") is None
    assert get_cipher(os.urandom(32).hex()) is not None
