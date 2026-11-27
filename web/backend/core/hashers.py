"""Пароли из прежней версии (users.json): PBKDF2-HMAC-SHA256, соль — 16 байт в hex, хеш — hex.

Формат в базе Django: legacy_pbkdf2$<итерации>$<соль hex>$<хеш hex>. После успешного входа Django
перехеширует пароль в свой стандартный формат (must_update) — пользователь этого не замечает.
"""
import hashlib
import hmac

from django.contrib.auth.hashers import BasePasswordHasher


class LegacyPBKDF2Hasher(BasePasswordHasher):
    algorithm = "legacy_pbkdf2"

    def encode(self, password, salt, iterations=200_000):
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
        return f"{self.algorithm}${iterations}${salt}${digest}"

    def decode(self, encoded):
        algorithm, iterations, salt, digest = encoded.split("$", 3)
        return {"algorithm": algorithm, "iterations": int(iterations), "salt": salt, "hash": digest}

    def verify(self, password, encoded):
        parts = self.decode(encoded)
        return hmac.compare_digest(self.encode(password, parts["salt"], parts["iterations"]), encoded)

    def safe_summary(self, encoded):
        return {"algorithm": self.algorithm}

    def must_update(self, encoded):
        return True  # сразу перевести на стандартный хешер Django

    def harden_runtime(self, password, encoded):
        pass


def from_legacy(salt_hex: str, hash_hex: str, iterations: int = 200_000) -> str:
    return f"{LegacyPBKDF2Hasher.algorithm}${iterations}${salt_hex}${hash_hex}"
