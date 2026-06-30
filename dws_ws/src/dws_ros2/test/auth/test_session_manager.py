import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "authentication"))

import pytest

from session_manager import (
    SessionManager,
    SessionInvalid,
    SessionExpired,
)


def _make_user(conn, username="testuser", role="operator"):
    role_row = conn.execute("SELECT id FROM roles WHERE name = ?", (role,)).fetchone()
    cur = conn.execute(
        """
        INSERT INTO auth_users (username, password_hash, role_id, is_active,
                                  must_change_password, created_at)
        VALUES (?, 'dummyhash', ?, 1, 0, datetime('now'))
        """,
        (username, role_row["id"]),
    )
    conn.commit()
    return cur.lastrowid


def test_create_and_validate_session(conn):
    user_id = _make_user(conn)
    sm = SessionManager(conn)
    token, info = sm.create_session(user_id=user_id, username="testuser", role="operator")
    assert info.user_id == user_id

    principal = sm.validate_session(token)
    assert principal.user_id == user_id
    assert principal.username == "testuser"
    assert principal.role == "operator"


def test_validate_unknown_token_raises_invalid(conn):
    sm = SessionManager(conn)
    with pytest.raises(SessionInvalid):
        sm.validate_session("this-token-was-never-issued")


def test_validate_empty_token_raises_invalid(conn):
    sm = SessionManager(conn)
    with pytest.raises(SessionInvalid):
        sm.validate_session("")


def test_logout_revokes_session(conn):
    user_id = _make_user(conn)
    sm = SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")

    revoked = sm.revoke_session(token)
    assert revoked is True

    with pytest.raises(SessionInvalid):
        sm.validate_session(token)


def test_logout_twice_is_idempotent(conn):
    user_id = _make_user(conn)
    sm = SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")
    assert sm.revoke_session(token) is True
    assert sm.revoke_session(token) is False  # already revoked, not an error


def test_multiple_concurrent_sessions_for_same_user(conn):
    user_id = _make_user(conn)
    sm = SessionManager(conn)
    token1, _ = sm.create_session(user_id=user_id, username="testuser", role="operator", station_id="STATION_01")
    token2, _ = sm.create_session(user_id=user_id, username="testuser", role="operator", station_id="STATION_02")

    assert token1 != token2
    # Both valid independently.
    sm.validate_session(token1)
    sm.validate_session(token2)

    # Revoking one does not affect the other.
    sm.revoke_session(token1)
    with pytest.raises(SessionInvalid):
        sm.validate_session(token1)
    sm.validate_session(token2)  # still fine


def test_revoke_all_sessions_for_user(conn):
    user_id = _make_user(conn)
    sm = SessionManager(conn)
    t1, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")
    t2, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")

    count = sm.revoke_all_sessions_for_user(user_id)
    assert count == 2

    with pytest.raises(SessionInvalid):
        sm.validate_session(t1)
    with pytest.raises(SessionInvalid):
        sm.validate_session(t2)


def test_session_expires_after_absolute_ttl(conn, monkeypatch):
    import session_manager as sm_module
    monkeypatch.setattr(sm_module, "SESSION_ABSOLUTE_TTL_SECONDS", 1)

    user_id = _make_user(conn)
    sm = sm_module.SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")

    sm.validate_session(token)  # valid immediately

    time.sleep(1.2)
    with pytest.raises(sm_module.SessionExpired):
        sm.validate_session(token)


def test_session_expires_after_idle_timeout(conn, monkeypatch):
    import session_manager as sm_module
    monkeypatch.setattr(sm_module, "SESSION_IDLE_TIMEOUT_SECONDS", 1)

    user_id = _make_user(conn)
    sm = sm_module.SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")

    sm.validate_session(token)
    time.sleep(1.2)
    with pytest.raises(sm_module.SessionExpired):
        sm.validate_session(token)


def test_touch_false_does_not_reset_idle_clock(conn, monkeypatch):
    import session_manager as sm_module
    monkeypatch.setattr(sm_module, "SESSION_IDLE_TIMEOUT_SECONDS", 1)

    user_id = _make_user(conn)
    sm = sm_module.SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")

    # touch=False repeatedly should NOT keep the session alive.
    sm.validate_session(token, touch=False)
    time.sleep(1.2)
    with pytest.raises(sm_module.SessionExpired):
        sm.validate_session(token, touch=False)


def test_disabled_user_session_becomes_invalid(conn):
    user_id = _make_user(conn)
    sm = SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")
    sm.validate_session(token)  # works while active

    conn.execute("UPDATE auth_users SET is_active = 0 WHERE id = ?", (user_id,))
    conn.commit()

    with pytest.raises(SessionInvalid):
        sm.validate_session(token)


def test_list_active_sessions_filters_correctly(conn):
    u1 = _make_user(conn, username="user_one")
    u2 = _make_user(conn, username="user_two")
    sm = SessionManager(conn)
    sm.create_session(user_id=u1, username="user_one", role="operator")
    sm.create_session(user_id=u2, username="user_two", role="operator")

    all_sessions = sm.list_active_sessions()
    assert len(all_sessions) == 2

    only_u1 = sm.list_active_sessions(user_id=u1)
    assert len(only_u1) == 1
    assert only_u1[0].user_id == u1


def test_purge_expired_sessions_removes_old_revoked_rows(conn, monkeypatch):
    import session_manager as sm_module
    user_id = _make_user(conn)
    sm = sm_module.SessionManager(conn)
    token, _ = sm.create_session(user_id=user_id, username="testuser", role="operator")
    sm.revoke_session(token)

    # Force created_at to look old enough to be eligible for purge.
    conn.execute("UPDATE auth_sessions SET created_at = created_at - 999999")
    conn.commit()

    deleted = sm.purge_expired_sessions(older_than_days=1)
    assert deleted == 1
    remaining = conn.execute("SELECT COUNT(*) AS n FROM auth_sessions").fetchone()["n"]
    assert remaining == 0
