import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "authentication"))

import pytest

from password_manager import (
    hash_password,
    verify_password,
    needs_rehash,
    validate_password_complexity,
    assert_password_complexity,
    PasswordPolicyError,
    generate_reset_token,
    hash_reset_token,
    BCRYPT_ROUNDS,
)


def test_hash_and_verify_roundtrip():
    h = hash_password("CorrectPassword123")
    assert verify_password("CorrectPassword123", h) is True


def test_verify_wrong_password_fails():
    h = hash_password("CorrectPassword123")
    assert verify_password("WrongPassword456", h) is False


def test_verify_empty_inputs_fail_closed():
    assert verify_password("", "somehash") is False
    assert verify_password("something", "") is False
    assert verify_password("", "") is False


def test_verify_malformed_hash_fails_closed_not_raises():
    # Garbage that is not a valid bcrypt hash must not raise.
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_hash_is_not_plaintext():
    h = hash_password("SuperSecret123")
    assert "SuperSecret123" not in h
    assert h.startswith("$2b$") or h.startswith("$2a$")


def test_two_hashes_of_same_password_differ():
    # bcrypt salts each hash independently.
    h1 = hash_password("SamePassword123")
    h2 = hash_password("SamePassword123")
    assert h1 != h2
    assert verify_password("SamePassword123", h1)
    assert verify_password("SamePassword123", h2)


def test_needs_rehash_detects_lower_cost():
    import bcrypt
    low_cost_hash = bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode("ascii")
    assert needs_rehash(low_cost_hash, rounds=BCRYPT_ROUNDS) is True


def test_needs_rehash_false_for_current_cost():
    h = hash_password("AnyPassword123")
    assert needs_rehash(h, rounds=BCRYPT_ROUNDS) is False


@pytest.mark.parametrize("password", [
    "short1",          # too short
    "alllettersnodigits",  # no digit
    "12345678901234",  # no letter
])
def test_complexity_rejects_bad_passwords(password):
    result = validate_password_complexity(password)
    assert result.ok is False
    assert len(result.problems) >= 1


def test_complexity_accepts_good_password():
    result = validate_password_complexity("GoodPassword123")
    assert result.ok is True
    assert result.problems == []


def test_complexity_rejects_password_containing_username():
    result = validate_password_complexity("jdoe12345678", username="jdoe")
    assert result.ok is False
    assert any("username" in p.lower() for p in result.problems)


def test_complexity_rejects_common_password():
    result = validate_password_complexity("password123")
    assert result.ok is False


def test_assert_complexity_raises():
    with pytest.raises(PasswordPolicyError):
        assert_password_complexity("bad")


def test_reset_token_generation_and_hash_consistency():
    raw, token_hash = generate_reset_token()
    assert raw != token_hash
    assert hash_reset_token(raw) == token_hash
    # Two generated tokens must differ (CSPRNG, not deterministic).
    raw2, hash2 = generate_reset_token()
    assert raw != raw2
    assert token_hash != hash2
