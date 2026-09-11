"""Examiner identities and password handling.

Chain of custody is meaningless without knowing *who* touched the evidence, so
every custody entry is attributed to an authenticated user.  Passwords are
stored as PBKDF2-HMAC-SHA256 with a per-user random salt.

PBKDF2 is used rather than bcrypt or argon2 purely so the whole tool installs
with ``pip install -r requirements.txt`` and no compiler.  The iteration count
is kept high and is stored per-user, so it can be raised later without
invalidating existing passwords.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
ROLES = ("admin", "examiner")

#: Length of a generated access key, in bytes of entropy.  Rendered as hex, so
#: the string the examiner pastes into the lock is twice this long.
ACCESS_KEY_BYTES = 24


class AuthError(RuntimeError):
    """Raised for an unusable credential or a duplicate username."""


@dataclass(frozen=True)
class Examiner:
    """An authenticated identity, safe to put in a session or a log entry."""

    id: int
    username: str
    full_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "role": self.role,
        }


def hash_password(password: str, *, salt: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str, int]:
    """Return ``(password_hash, salt, iterations)`` for a plaintext password."""
    if not password:
        raise AuthError("password must not be empty")
    salt = salt or secrets.token_hex(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations
    )
    return derived.hex(), salt, iterations


def verify_password(password: str, password_hash: str, salt: str, iterations: int) -> bool:
    """Constant-time check of a plaintext password against a stored hash."""
    try:
        candidate, _, _ = hash_password(password, salt=salt, iterations=iterations)
    except (AuthError, ValueError):
        return False
    return hmac.compare_digest(candidate, password_hash)


def generate_access_key() -> str:
    """Mint a random access key for an examiner to paste into the lock."""
    return secrets.token_hex(ACCESS_KEY_BYTES)


def examiner_from_row(row: sqlite3.Row | None) -> Examiner | None:
    if row is None:
        return None
    return Examiner(
        id=int(row["id"]),
        username=str(row["username"]),
        full_name=str(row["full_name"]),
        role=str(row["role"]),
    )
