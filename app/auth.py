"""Password hashing with stdlib only — pbkdf2-sha256, salted, constant-time verify.

Standalone (no db/service imports) so both db.py (admin seed) and service.py
(register/login) can use it without a circular import.
"""
import hashlib
import hmac
import secrets

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, dk_hex = stored.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITERATIONS)
    return hmac.compare_digest(dk.hex(), dk_hex)


if __name__ == "__main__":  # ponytail: one runnable check for the security path
    h = hash_password("admin")
    assert verify_password("admin", h)
    assert not verify_password("wrong", h)
    assert not verify_password("admin", None)
    assert hash_password("x") != hash_password("x")  # random salt
    print("ok")
