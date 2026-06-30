"""
password_manager.py
====================

All password-related cryptography for the DWS authentication
subsystem: hashing, verification, complexity validation, and
one-time reset tokens.

Hashing
-------
Uses bcrypt directly (not passlib) to keep the dependency surface
small and match the spec's explicit "bcrypt password hashing"
requirement. bcrypt's own random salt generation is used — no
separate salt column is needed, the salt is embedded in the hash
string bcrypt returns.

Nothing in this module ever logs, prints, or persists a plaintext
password. Callers (auth_manager.py, user_manager.py) are responsible
for not doing so either.
"""

from __future__ import annotations

import re
import secrets
import hashlib
from dataclasses import dataclass

import bcrypt


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

# bcrypt cost factor. 12 is a reasonable default for a server-class
# machine in 2026 (roughly 200-300ms per hash) — high enough to resist
# offline brute force on a stolen DB, low enough not to make login
# feel slow on commodity hardware (Intel NUC i5, per README hardware
# requirements). Raise this over time as hardware improves; bcrypt
# hashes embed their own cost factor so raising it never invalidates
# existing stored hashes (they just keep verifying at the old cost
# until the user's password is next changed).
BCRYPT_ROUNDS = 12

MIN_PASSWORD_LENGTH = 10

# Reset tokens are single-use, short-lived, and never emailed/printed
# in this module — issuing them to the operator (e.g. via an admin
# screen, or a future SMTP integration) is out of scope here. This
# module only generates, hashes, and validates them.
RESET_TOKEN_BYTES = 32
RESET_TOKEN_TTL_SECONDS = 30 * 60  # 30 minutes


class PasswordPolicyError(ValueError):
    """Raised when a candidate password fails complexity validation."""


@dataclass(frozen=True)
class PasswordPolicyResult:
    ok: bool
    problems: list[str]


# ---------------------------------------------------------------------------
# Complexity validation
# ---------------------------------------------------------------------------

def validate_password_complexity(
    password: str,
    *,
    username: str | None = None,
) -> PasswordPolicyResult:
    """
    Industrial-floor-appropriate complexity policy:
      - at least MIN_PASSWORD_LENGTH characters
      - at least one letter and one digit
      - not identical to (or a trivial substring match of) the username
      - not in a short list of catastrophically common passwords

    Deliberately does NOT require special characters. Forcing symbols
    on a warehouse floor where operators may be typing on a shared
    keyboard or a barcode-scanner-adjacent terminal tends to push
    people toward writing passwords on a sticky note, which is a
    worse outcome than a slightly larger character set. Length +
    lockout + bcrypt cost do most of the real work here.
    """
    problems: list[str] = []

    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")

    if not re.search(r"[A-Za-z]", password):
        problems.append("Password must contain at least one letter.")

    if not re.search(r"[0-9]", password):
        problems.append("Password must contain at least one digit.")

    if username and username.strip().lower() in password.lower():
        problems.append("Password must not contain the username.")

    if password.lower() in _COMMON_PASSWORDS:
        problems.append("This password is too common. Choose another.")

    return PasswordPolicyResult(ok=not problems, problems=problems)


def assert_password_complexity(password: str, *, username: str | None = None) -> None:
    """Same check as validate_password_complexity, raising on failure."""
    result = validate_password_complexity(password, username=username)
    if not result.ok:
        raise PasswordPolicyError(" ".join(result.problems))


_COMMON_PASSWORDS = {
    "password1", "password123", "12345678", "123456789", "qwerty123",
    "administrator", "admin1234", "delhivery1", "changeme1", "welcome123",
}


# ---------------------------------------------------------------------------
# Hashing / verification
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    Return a bcrypt hash (as a str, ASCII-safe) suitable for storing
    in auth_users.password_hash.
    """
    if not isinstance(password, str) or password == "":
        raise ValueError("password must be a non-empty string")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    digest = bcrypt.hashpw(password.encode("utf-8"), salt)
    return digest.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Constant-time-safe verification (delegated to bcrypt.checkpw,
    which compares digests rather than raw bytes). Returns False
    (never raises) on malformed stored hashes, so a corrupted row
    fails closed instead of crashing the login endpoint.
    """
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def needs_rehash(password_hash: str, *, rounds: int = BCRYPT_ROUNDS) -> bool:
    """
    True if a stored hash was created with a lower cost factor than
    the current BCRYPT_ROUNDS. auth_manager.py calls this right after
    a successful login and, if true, re-hashes the password the user
    just supplied — a standard "upgrade hashes opportunistically"
    pattern that needs no forced reset and no extra user-facing step.
    """
    try:
        # bcrypt hash format: $2b$<cost>$<22-char-salt><31-char-hash>
        parts = password_hash.split("$")
        stored_rounds = int(parts[2])
        return stored_rounds < rounds
    except (IndexError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------------
# Tokens are generated with secrets.token_urlsafe (CSPRNG), and only
# their SHA-256 hash is ever persisted — identical principle to
# session tokens in session_manager.py. The raw token is returned
# once, to the caller (an admin action), and never stored.

def generate_reset_token() -> tuple[str, str]:
    """
    Returns (raw_token, token_hash). Store token_hash; hand raw_token
    to the user out-of-band (this module does not deliver it).
    """
    raw = secrets.token_urlsafe(RESET_TOKEN_BYTES)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
