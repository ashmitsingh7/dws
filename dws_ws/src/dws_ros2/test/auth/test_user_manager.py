import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "authentication"))

import pytest

from user_manager import (
    UserManager,
    UsernameTakenError,
    UserNotFoundError,
    UnknownRoleError,
    InsufficientPrivilegeError,
    LastAdministratorError,
)
from password_manager import PasswordPolicyError


def test_create_user_success(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    assert user.username == "jdoe"
    assert user.role == "operator"
    assert user.is_active is True
    assert user.must_change_password is True  # default


def test_create_user_duplicate_username_raises(conn):
    um = UserManager(conn)
    um.create_user(username="jdoe", password="StrongPass123", role="operator")
    with pytest.raises(UsernameTakenError):
        um.create_user(username="jdoe", password="AnotherPass456", role="operator")


def test_create_user_unknown_role_raises(conn):
    um = UserManager(conn)
    with pytest.raises(UnknownRoleError):
        um.create_user(username="jdoe", password="StrongPass123", role="superadmin")


def test_create_user_weak_password_raises(conn):
    um = UserManager(conn)
    with pytest.raises(PasswordPolicyError):
        um.create_user(username="jdoe", password="weak", role="operator")


def test_create_user_password_hash_never_equals_plaintext(conn):
    um = UserManager(conn)
    um.create_user(username="jdoe", password="StrongPass123", role="operator")
    stored = um.get_password_hash("jdoe")
    assert stored != "StrongPass123"
    assert stored.startswith("$2")


def test_supervisor_cannot_create_administrator(conn):
    um = UserManager(conn)
    with pytest.raises(InsufficientPrivilegeError):
        um.create_user(
            username="sneaky", password="StrongPass123", role="administrator",
            actor_role="supervisor",
        )


def test_administrator_can_create_administrator(conn):
    um = UserManager(conn)
    user = um.create_user(
        username="newadmin", password="StrongPass123", role="administrator",
        actor_role="administrator",
    )
    assert user.role == "administrator"


def test_get_user_not_found_raises(conn):
    um = UserManager(conn)
    with pytest.raises(UserNotFoundError):
        um.get_user(99999)


def test_get_user_by_username_not_found_raises(conn):
    um = UserManager(conn)
    with pytest.raises(UserNotFoundError):
        um.get_user_by_username("nobody")


def test_list_users_includes_bootstrap_admin(conn, admin_password):
    um = UserManager(conn)
    users = um.list_users()
    usernames = {u.username for u in users}
    assert "admin" in usernames


def test_update_user_display_name(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    updated = um.update_user(user.id, display_name="Jane Doe")
    assert updated.display_name == "Jane Doe"


def test_update_user_role(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    updated = um.update_user(user.id, role="supervisor")
    assert updated.role == "supervisor"


def test_update_user_unknown_role_raises(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    with pytest.raises(UnknownRoleError):
        um.update_user(user.id, role="superadmin")


def test_change_password_updates_hash_and_clears_lockout(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    old_hash = um.get_password_hash("jdoe")

    um.record_failed_attempt("jdoe")
    um.lock_account(user.id, duration_seconds=900)

    um.change_password(user.id, "BrandNewPass456")
    new_hash = um.get_password_hash("jdoe")
    assert new_hash != old_hash

    refreshed = um.get_user(user.id)
    assert refreshed.failed_attempts == 0
    assert refreshed.locked_until is None


def test_change_password_weak_password_raises(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    with pytest.raises(PasswordPolicyError):
        um.change_password(user.id, "weak")


def test_disable_and_enable_user(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    um.disable_user(user.id)
    assert um.get_user(user.id).is_active is False

    um.enable_user(user.id)
    assert um.get_user(user.id).is_active is True


def test_disable_user_clears_lockout_on_enable(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    um.lock_account(user.id, duration_seconds=900)
    um.disable_user(user.id)
    um.enable_user(user.id)
    refreshed = um.get_user(user.id)
    assert refreshed.locked_until is None
    assert refreshed.failed_attempts == 0


def test_delete_user(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    um.delete_user(user.id)
    with pytest.raises(UserNotFoundError):
        um.get_user(user.id)


def test_cannot_disable_last_administrator(conn):
    um = UserManager(conn)
    admin = um.get_user_by_username("admin")
    with pytest.raises(LastAdministratorError):
        um.disable_user(admin.id)


def test_cannot_delete_last_administrator(conn):
    um = UserManager(conn)
    admin = um.get_user_by_username("admin")
    with pytest.raises(LastAdministratorError):
        um.delete_user(admin.id)


def test_cannot_demote_last_administrator(conn):
    um = UserManager(conn)
    admin = um.get_user_by_username("admin")
    with pytest.raises(LastAdministratorError):
        um.update_user(admin.id, role="operator")


def test_can_disable_administrator_if_another_exists(conn):
    um = UserManager(conn)
    admin = um.get_user_by_username("admin")
    um.create_user(username="admin2", password="StrongPass123", role="administrator")
    # Now disabling the *original* admin should succeed since admin2 exists.
    um.disable_user(admin.id)
    assert um.get_user(admin.id).is_active is False


def test_record_failed_attempt_increments_and_unknown_user_is_noop(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    count1 = um.record_failed_attempt("jdoe")
    count2 = um.record_failed_attempt("jdoe")
    assert count1 == 1
    assert count2 == 2

    # Unknown username: must not raise, must not leak existence via return value semantics.
    result = um.record_failed_attempt("totally_made_up_user")
    assert result == 0


def test_reset_failed_attempts(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    um.record_failed_attempt("jdoe")
    um.lock_account(user.id, duration_seconds=900)
    um.reset_failed_attempts(user.id)
    refreshed = um.get_user(user.id)
    assert refreshed.failed_attempts == 0
    assert refreshed.locked_until is None


def test_is_locked_reflects_locked_until(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    assert um.is_locked(user) is False

    um.lock_account(user.id, duration_seconds=900)
    refreshed = um.get_user(user.id)
    assert um.is_locked(refreshed) is True


def test_record_login_success_clears_lockout_state(conn):
    um = UserManager(conn)
    user = um.create_user(username="jdoe", password="StrongPass123", role="operator")
    um.record_failed_attempt("jdoe")
    um.record_login_success(user.id)
    refreshed = um.get_user(user.id)
    assert refreshed.failed_attempts == 0
    assert refreshed.locked_until is None
    assert refreshed.last_login_at is not None
