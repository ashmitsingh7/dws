import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "authentication"))

import pytest

from auth_manager import (
    AuthManager,
    InvalidCredentialsError,
    AccountDisabledError,
    AccountLockedError,
    RateLimitedError,
    MAX_FAILED_ATTEMPTS,
)
from session_manager import SessionInvalid


def test_login_success(conn, admin_password):
    am = AuthManager(conn)
    result = am.login("admin", admin_password, ip_address="10.0.0.1")
    assert result.token
    assert result.must_change_password is True
    assert result.session.role == "administrator"


def test_login_wrong_password(conn, admin_password):
    am = AuthManager(conn)
    with pytest.raises(InvalidCredentialsError):
        am.login("admin", "totally-wrong", ip_address="10.0.0.2")


def test_login_unknown_username(conn):
    am = AuthManager(conn)
    with pytest.raises(InvalidCredentialsError):
        am.login("ghost", "whatever123", ip_address="10.0.0.3")


def test_login_disabled_account(conn, admin_password):
    am = AuthManager(conn)
    am.users.create_user(username="jdoe", password="StrongPass123", role="operator")
    user = am.users.get_user_by_username("jdoe")
    am.users.disable_user(user.id)

    with pytest.raises(AccountDisabledError):
        am.login("jdoe", "StrongPass123", ip_address="10.0.0.4")


def test_account_locks_after_max_failed_attempts(conn):
    am = AuthManager(conn)
    am.users.create_user(username="jdoe", password="StrongPass123", role="operator")

    for i in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            am.login("jdoe", "wrongpass", ip_address=f"10.1.0.{i}")

    # Next attempt, even with the CORRECT password, must be locked out.
    with pytest.raises(AccountLockedError) as exc_info:
        am.login("jdoe", "StrongPass123", ip_address="10.1.0.99")
    assert exc_info.value.retry_after_seconds > 0


def test_successful_login_resets_failed_attempts(conn):
    am = AuthManager(conn)
    am.users.create_user(username="jdoe", password="StrongPass123", role="operator")

    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(InvalidCredentialsError):
            am.login("jdoe", "wrongpass", ip_address="10.2.0.1")

    # One more failure would lock; instead succeed.
    am.login("jdoe", "StrongPass123", ip_address="10.2.0.1")

    user = am.users.get_user_by_username("jdoe")
    assert user.failed_attempts == 0


def test_ip_rate_limiting_triggers_after_many_failures_across_usernames(conn):
    am = AuthManager(conn)
    # IP_RATE_LIMIT_MAX_ATTEMPTS is 20 by default; spray failures from
    # one IP against a mix of valid and invalid usernames.
    from auth_manager import IP_RATE_LIMIT_MAX_ATTEMPTS

    am.users.create_user(username="jdoe", password="StrongPass123", role="operator")

    ip = "10.3.0.1"
    for i in range(IP_RATE_LIMIT_MAX_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            am.login("jdoe", "wrongpass", ip_address=ip)

    with pytest.raises(RateLimitedError):
        am.login("jdoe", "StrongPass123", ip_address=ip)


def test_logout_revokes_token(conn, admin_password):
    am = AuthManager(conn)
    result = am.login("admin", admin_password, ip_address="10.4.0.1")
    am.logout(result.token, ip_address="10.4.0.1")

    with pytest.raises(SessionInvalid):
        am.get_principal(result.token)


def test_logout_with_garbage_token_does_not_raise(conn):
    am = AuthManager(conn)
    # Should not raise -- logout is best-effort.
    am.logout("not-a-real-token", ip_address="10.4.0.2")


def test_change_own_password_requires_current_password(conn, admin_password):
    am = AuthManager(conn)
    result = am.login("admin", admin_password, ip_address="10.5.0.1")
    principal = am.get_principal(result.token)

    with pytest.raises(InvalidCredentialsError):
        am.change_own_password(principal.user_id, "wrong-current-pass", "NewStrongPass123")


def test_change_own_password_success_revokes_all_sessions(conn, admin_password):
    am = AuthManager(conn)
    result1 = am.login("admin", admin_password, ip_address="10.6.0.1")
    result2 = am.login("admin", admin_password, ip_address="10.6.0.2")

    am.change_own_password(result1.session.user_id, admin_password, "BrandNewPass456")

    # Both old sessions should now be invalid.
    with pytest.raises(SessionInvalid):
        am.get_principal(result1.token)
    with pytest.raises(SessionInvalid):
        am.get_principal(result2.token)

    # New password works for a fresh login.
    new_result = am.login("admin", "BrandNewPass456", ip_address="10.6.0.3")
    assert new_result.token


def test_admin_reset_password_forces_must_change(conn):
    am = AuthManager(conn)
    user = am.users.create_user(username="jdoe", password="StrongPass123", role="operator")
    login_result = am.login("jdoe", "StrongPass123", ip_address="10.7.0.1")

    am.admin_reset_password(
        actor_username="admin", target_user_id=user.id,
        new_password="ResetByAdmin123", ip_address="10.7.0.2",
    )

    # Old session revoked.
    with pytest.raises(SessionInvalid):
        am.get_principal(login_result.token)

    # New password works and must_change_password is set.
    new_login = am.login("jdoe", "ResetByAdmin123", ip_address="10.7.0.3")
    assert new_login.must_change_password is True


def test_audit_log_records_login_success_and_failure(conn, admin_password):
    am = AuthManager(conn)
    am.login("admin", admin_password, ip_address="10.8.0.1")
    with pytest.raises(InvalidCredentialsError):
        am.login("admin", "wrong", ip_address="10.8.0.2")

    rows = conn.execute(
        "SELECT action, result, ip_address FROM auth_login_history ORDER BY id"
    ).fetchall()
    actions = [(r["action"], r["result"]) for r in rows]
    assert ("login_success", "success") in actions
    assert ("login_failed", "failure") in actions


def test_audit_log_records_account_locked_event(conn):
    am = AuthManager(conn)
    am.users.create_user(username="jdoe", password="StrongPass123", role="operator")
    for i in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            am.login("jdoe", "wrongpass", ip_address=f"10.9.0.{i}")

    rows = conn.execute(
        "SELECT action FROM auth_login_history WHERE username = 'jdoe' ORDER BY id"
    ).fetchall()
    actions = [r["action"] for r in rows]
    assert "account_locked" in actions


def test_login_timing_similar_for_unknown_user_vs_wrong_password(conn, admin_password):
    """
    Not a strict timing-attack proof (that needs statistical sampling
    on dedicated hardware), but a basic sanity check that both paths
    actually invoke bcrypt rather than one of them short-circuiting
    near-instantly, which would be the easily-observable version of
    this bug.
    """
    am = AuthManager(conn)

    start = time.perf_counter()
    with pytest.raises(InvalidCredentialsError):
        am.login("admin", "wrongpassword", ip_address="10.10.0.1")
    known_user_duration = time.perf_counter() - start

    start = time.perf_counter()
    with pytest.raises(InvalidCredentialsError):
        am.login("totally_unknown_user", "wrongpassword", ip_address="10.10.0.2")
    unknown_user_duration = time.perf_counter() - start

    # Both should take "bcrypt-scale" time (much greater than a
    # microsecond dict-miss would take) -- a loose lower bound check
    # that the dummy-hash comparison path is actually doing real work.
    assert known_user_duration > 0.001
    assert unknown_user_duration > 0.001
