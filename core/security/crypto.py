"""At-rest encryption for chunk text (AES-256-GCM).

Enabled by setting EURAG_ENCRYPTION_KEY to 64 hex chars (32 bytes); absent
key means plaintext (local single-user default). Ciphertext is marked with a
version prefix so mixed plaintext/encrypted rows coexist — enabling the key
later encrypts new writes without breaking old reads, and a from-scratch
reseed re-encrypts everything.

Deliberately NOT encrypted: document titles and source URLs (they are the
citation surface and, for this corpus, public by nature), and the in-memory
BM25 index (search needs plaintext at runtime; at-rest is the boundary).
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "enc1:"


class ChunkCipher:
    def __init__(self, key_hex: str):
        key = bytes.fromhex(key_hex)
        if len(key) != 32:
            raise ValueError("EURAG_ENCRYPTION_KEY must be 64 hex chars (32 bytes)")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), None)
        return _PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, stored: str) -> str:
        if not stored.startswith(_PREFIX):
            return stored  # plaintext row from before the key was enabled
        raw = base64.b64decode(stored[len(_PREFIX):])
        return self._aead.decrypt(raw[:12], raw[12:], None).decode("utf-8")


def get_cipher(key_hex: str | None) -> ChunkCipher | None:
    return ChunkCipher(key_hex) if key_hex else None
